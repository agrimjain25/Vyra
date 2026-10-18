"""Builds the configured providers and runs structured-JSON requests with retries and fallback."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from ..config import Settings
from ..logging_setup import log
from ..repo import Repo
from .base import ImageProvider, LLMProvider, ProviderError, extract_json
from .cloudflare import CloudflareImageProvider
from .gemma import GemmaTextProvider
from .openai_compat import OpenAICompatProvider
from .pollinations import PollinationsImageProvider

logger = logging.getLogger("vyra.providers")
T = TypeVar("T", bound=BaseModel)


class GenerationFailed(Exception):
    """Every configured provider failed. ``attempts`` lists each failure for the UI and the logs."""

    def __init__(self, code: str, message: str, attempts: list[dict]):
        super().__init__(message)
        self.code = code
        self.message = message
        self.attempts = attempts

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "attempts": self.attempts}


@dataclass
class Outcome:
    parsed: BaseModel
    provider: str
    model: str
    fallback_used: bool
    attempts: int
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int
    cost_usd: float | None
    failures: list[dict] = field(default_factory=list)


class ProviderHub:
    def __init__(self, settings: Settings, repo: Repo):
        self.s = settings
        self.repo = repo
        self._http = httpx.AsyncClient()
        self.llms: dict[str, LLMProvider] = {}
        s = settings
        self.llms["amd"] = OpenAICompatProvider(
            name="amd", label="AMD-hosted model", base_url=s.amd_base_url, api_key=s.amd_api_key, model=s.amd_model,
            json_mode=s.amd_json_mode, price_in=s.amd_price_in_per_mtok, price_out=s.amd_price_out_per_mtok,
            missing_hint="Copy the base URL, API key and model name from your AMD Token Factory page into "
                         "AMD_BASE_URL, AMD_API_KEY and AMD_MODEL in backend/.env.", client=self._http)
        if s.gemma_openai_base_url:  # optional: Gemma served by you (e.g. vLLM on AMD Developer Cloud)
            self.llms["gemma"] = OpenAICompatProvider(
                name="gemma", label="Gemma (OpenAI-compatible endpoint)", base_url=s.gemma_openai_base_url,
                api_key=s.gemma_openai_api_key or s.gemma_api_key, model=s.gemma_model, json_mode="none",
                price_in=s.gemma_price_in_per_mtok, price_out=s.gemma_price_out_per_mtok,
                missing_hint="Set GEMMA_OPENAI_BASE_URL, GEMMA_OPENAI_API_KEY and GEMMA_MODEL in backend/.env.",
                client=self._http)
        else:
            self.llms["gemma"] = GemmaTextProvider(s, client=self._http)
        evolus_model = f"{s.evolus_app_id}:{s.evolus_agent_id}" if s.evolus_app_id and s.evolus_agent_id else None
        self.llms["evolus"] = OpenAICompatProvider(
            name="evolus", label="Evolus agent", base_url=s.evolus_base_url, api_key=s.evolus_api_key,
            model=evolus_model, json_mode="none",
            missing_hint="Set EVOLUS_API_KEY, EVOLUS_APP_ID and EVOLUS_AGENT_ID (from your Evolus workspace).",
            client=self._http)
        # Second Evolus agent, used only as the translation fallback (it is recorded under the name "evolus").
        # If EVOLUS_TRANSLATOR_AGENT_ID is not set, the review agent is used for that fallback.
        tr_agent = s.evolus_translator_agent_id or s.evolus_agent_id
        self.llms["evolus_translate"] = OpenAICompatProvider(
            name="evolus", label="Evolus agent (translation)", base_url=s.evolus_base_url, api_key=s.evolus_api_key,
            model=f"{s.evolus_app_id}:{tr_agent}" if s.evolus_app_id and tr_agent else None, json_mode="none",
            missing_hint="Set EVOLUS_API_KEY, EVOLUS_APP_ID and EVOLUS_AGENT_ID (optionally EVOLUS_TRANSLATOR_AGENT_ID).",
            client=self._http)
        image_cls = {"cloudflare": CloudflareImageProvider, "pollinations": PollinationsImageProvider}.get(
            s.image_provider, CloudflareImageProvider)
        self.image: ImageProvider = image_cls(s, client=self._http)
        # Backup image provider: used ONLY when the main one (Cloudflare) fails. None if main is already Pollinations.
        self.image_backup: ImageProvider | None = (
            PollinationsImageProvider(s, client=self._http) if self.image.name != "pollinations" else None)
        self.llm_sem = asyncio.Semaphore(max(1, s.llm_concurrency))
        self.image_sem = asyncio.Semaphore(max(1, s.image_concurrency))

    def image_ready(self) -> tuple[bool, str | None]:
        """Images can be generated if the main provider OR the backup is configured."""
        ok, why = self.image.configured()
        if ok:
            return True, None
        if self.image_backup is not None and self.image_backup.configured()[0]:
            return True, None
        return False, why

    # ------------------------------------------------------------------ routing
    def chain_names(self, purpose: str) -> list[str]:
        s = self.s
        if purpose == "translate":
            # Translation: Gemma first; if Gemma fails, the Evolus agent translates.
            return ["gemma", "evolus_translate"]
        order = []
        if purpose == "review" and s.review_llm not in ("", "none"):
            # Review: the review provider (Evolus) first, then the configured fallback (Gemma), then the primary (AMD).
            order += [s.review_llm, s.fallback_llm, s.primary_llm]
        else:
            order += [s.primary_llm, s.fallback_llm]
        seen: list[str] = []
        for n in order:
            if n and n != "none" and n not in seen:
                seen.append(n)
        return seen

    def chain(self, purpose: str) -> list[LLMProvider]:
        return [self.llms[n] for n in self.chain_names(purpose)]

    # ------------------------------------------------------------------ structured completion
    async def run_json(self, *, purpose: str, system: str, user: str, model_cls: type[T],
                       campaign_id: str | None = None, experience_id: str | None = None,
                       max_tokens: int | None = None, temperature: float | None = None,
                       chain_purpose: str | None = None) -> Outcome:
        s = self.s
        providers = self.chain(chain_purpose or purpose)
        if not providers:
            raise GenerationFailed("no_provider", "No AI provider is selected. Set PRIMARY_LLM_PROVIDER in backend/.env.", [])
        failures: list[dict] = []
        tot_in: int | None = None
        tot_out: int | None = None
        cost_total: float | None = 0.0
        attempts = 0
        for idx, prov in enumerate(providers):
            ok, why = prov.configured()
            if not ok:
                failures.append({"provider": prov.name, "code": "missing_credentials", "message": why})
                log(logger, logging.WARNING, "provider not configured", provider=prov.name, purpose=purpose)
                continue
            correction = ""
            for attempt in range(1, s.llm_max_retries + 2):
                attempts += 1
                try:
                    async with self.llm_sem:
                        res = await prov.complete(
                            system, user + correction, json_mode=True,
                            max_tokens=max_tokens or s.llm_max_output_tokens,
                            temperature=s.llm_temperature if temperature is None else temperature,
                            timeout=s.llm_timeout_s)
                except ProviderError as exc:
                    self.repo.add_call(purpose=purpose, provider=prov.name, model=prov.model,
                                       endpoint_host=prov.endpoint_host, ok=False, error_code=exc.code,
                                       error_message=exc.message, campaign_id=campaign_id,
                                       experience_id=experience_id, attempt=attempts, fallback_used=idx > 0)
                    failures.append({"provider": prov.name, "model": prov.model, **{k: v for k, v in exc.as_dict().items() if k != "provider"}})
                    log(logger, logging.WARNING, "provider call failed", provider=prov.name, purpose=purpose,
                        code=exc.code, attempt=attempt)
                    if exc.retryable and attempt <= s.llm_max_retries:
                        await asyncio.sleep(min(exc.retry_after or (1.0 * 2 ** (attempt - 1)), 20.0))
                        continue
                    break
                # The HTTP call worked: account for tokens and cost whether or not the content is usable.
                cost = prov.cost(res.input_tokens, res.output_tokens)
                if res.input_tokens is not None:
                    tot_in = (tot_in or 0) + res.input_tokens
                if res.output_tokens is not None:
                    tot_out = (tot_out or 0) + res.output_tokens
                cost_total = None if (cost is None or cost_total is None) else cost_total + cost
                try:
                    parsed = model_cls.model_validate(extract_json(res.text))
                except (ValueError, ValidationError) as exc:
                    detail = _short(exc)
                    self.repo.add_call(purpose=purpose, provider=prov.name, model=res.model,
                                       endpoint_host=res.endpoint_host, ok=True, error_code="invalid_output",
                                       error_message=detail, latency_ms=res.latency_ms,
                                       input_tokens=res.input_tokens, output_tokens=res.output_tokens, cost_usd=cost,
                                       request_id=res.request_id, campaign_id=campaign_id,
                                       experience_id=experience_id, attempt=attempts, fallback_used=idx > 0)
                    failures.append({"provider": prov.name, "model": prov.model, "code": "invalid_output",
                                     "message": f"The model's reply did not match the required format: {detail}"})
                    correction = ("\n\nYour previous reply was rejected: " + detail +
                                  "\nReturn ONLY one valid JSON object that follows the required structure exactly.")
                    if attempt <= s.llm_max_retries:
                        continue
                    break
                self.repo.add_call(purpose=purpose, provider=prov.name, model=res.model, endpoint_host=res.endpoint_host,
                                   ok=True, latency_ms=res.latency_ms, input_tokens=res.input_tokens,
                                   output_tokens=res.output_tokens, cost_usd=cost, request_id=res.request_id,
                                   campaign_id=campaign_id, experience_id=experience_id, attempt=attempts,
                                   fallback_used=idx > 0)
                return Outcome(parsed=parsed, provider=prov.name, model=res.model, fallback_used=idx > 0,
                               attempts=attempts, input_tokens=tot_in, output_tokens=tot_out,
                               latency_ms=res.latency_ms, cost_usd=cost_total, failures=failures)
        raise GenerationFailed(_failure_code(failures), _failure_message(failures), failures)

    async def aclose(self) -> None:
        await self._http.aclose()


def _short(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        errs = exc.errors(include_url=False, include_context=False, include_input=False)[:4]
        return "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in errs)[:400]
    return str(exc)[:400]


def _failure_code(failures: list[dict]) -> str:
    codes = [f.get("code") for f in failures]
    if codes and all(c == "missing_credentials" for c in codes):
        return "no_provider_configured"
    for c in ("quota_exhausted", "auth_failed"):
        if c in codes:
            return c
    return codes[-1] if codes else "generation_failed"


def _failure_message(failures: list[dict]) -> str:
    if not failures:
        return "Generation failed."
    if all(f.get("code") == "missing_credentials" for f in failures):
        return " ".join(dict.fromkeys(str(f.get("message")) for f in failures))
    parts = []
    for f in failures:
        if f.get("code") != "missing_credentials":
            parts.append(f"{f.get('provider')}: {f.get('message')}")
    return "Could not generate content. " + " | ".join(dict.fromkeys(parts))[:700]
