"""Adapter for any OpenAI-compatible ``/chat/completions`` endpoint.

Used for:
  * ``amd``           - an AMD-hosted endpoint (AMD Token Factory, or your own vLLM on AMD Developer Cloud)
  * ``openai_compat`` - any other OpenAI-compatible service (optional)
  * ``evolus``        - an Evolus agent through Evolus' OpenAI-compatible chat endpoint

Nothing here proves *where* the model runs. The status page only reports what was configured and what
was actually observed (endpoint host, model, latency, token counts).
"""
from __future__ import annotations

import time

import httpx

from .base import LLMProvider, LLMResult, ProviderError, host_of, post_json


class OpenAICompatProvider(LLMProvider):
    def __init__(self, *, name: str, label: str, base_url: str | None, api_key: str | None, model: str | None,
                 json_mode: str = "json_object", price_in: float | None = None, price_out: float | None = None,
                 missing_hint: str = "", client: httpx.AsyncClient | None = None):
        self.name = name
        self.label = label
        self.base_url = (base_url or "").rstrip("/")
        self._api_key = api_key
        self.model = model or ""
        self.json_mode = json_mode
        self.price_in_per_mtok = price_in
        self.price_out_per_mtok = price_out
        self._missing_hint = missing_hint
        self._client = client or httpx.AsyncClient()
        self._json_unsupported = False

    @property
    def endpoint_host(self) -> str:
        return host_of(self.base_url)

    def configured(self) -> tuple[bool, str | None]:
        missing = [n for n, v in (("base URL", self.base_url), ("API key", self._api_key), ("model", self.model)) if not v]
        if missing:
            return False, f"{self.label} is not configured (missing {', '.join(missing)}). {self._missing_hint}".strip()
        return True, None

    async def complete(self, system: str, user: str, *, json_mode: bool, max_tokens: int,
                       temperature: float, timeout: float) -> LLMResult:
        ok, why = self.configured()
        if not ok:
            raise ProviderError(self.name, "missing_credentials", why or "not configured")
        use_json = json_mode and self.json_mode == "json_object" and not self._json_unsupported
        try:
            return await self._call(system, user, use_json, max_tokens, temperature, timeout)
        except ProviderError as exc:
            # Some servers reject response_format. Remember that and retry once without it.
            if use_json and exc.code == "bad_request" and "response_format" in exc.message.lower():
                self._json_unsupported = True
                return await self._call(system, user, False, max_tokens, temperature, timeout)
            raise

    async def _call(self, system: str, user: str, use_json: bool, max_tokens: int, temperature: float,
                    timeout: float) -> LLMResult:
        payload: dict = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if use_json:
            payload["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
        started = time.perf_counter()
        data, resp = await post_json(self._client, self.name, self.label, f"{self.base_url}/chat/completions",
                                     headers, payload, timeout)
        latency = round((time.perf_counter() - started) * 1000)

        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ProviderError(self.name, "bad_response", f"{self.label} returned no choices.", retryable=True)
        choice = choices[0] or {}
        message = choice.get("message") or {}
        content = message.get("content")
        if isinstance(content, list):  # some servers return content parts
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        if choice.get("finish_reason") == "length":
            raise ProviderError(self.name, "truncated",
                                f"{self.label} stopped before finishing (output length limit). Retrying may help.",
                                retryable=True)
        if not isinstance(content, str) or not content.strip():
            raise ProviderError(self.name, "bad_response", f"{self.label} returned an empty message.", retryable=True)

        usage = data.get("usage") or {}
        in_tok = usage.get("prompt_tokens")
        out_tok = usage.get("completion_tokens")
        return LLMResult(
            text=content, provider=self.name, model=str(data.get("model") or self.model),
            input_tokens=in_tok if isinstance(in_tok, int) else None,
            output_tokens=out_tok if isinstance(out_tok, int) else None,
            latency_ms=latency, request_id=str(data.get("id") or resp.headers.get("x-request-id") or "") or None,
            endpoint_host=self.endpoint_host)

    async def aclose(self) -> None:
        await self._client.aclose()
