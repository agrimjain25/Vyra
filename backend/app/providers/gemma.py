"""Gemma adapter (TEXT ONLY). Gemma is Google DeepMind's open-weights model family.

Default route: Gemma served by the Google AI Studio / Gemini API (``generateContent`` endpoint, model id such as
``gemma-4-31b-it``). Gemma is a different model from Gemini even though the same API and the same AI Studio key serve it.

If ``GEMMA_OPENAI_BASE_URL`` is set, ``hub.py`` uses an OpenAI-compatible client instead (for example your own vLLM
server on AMD Developer Cloud) and this class is not used.

Gemma on the hosted API may reject a system instruction or JSON response mode. When the API says so (HTTP 400 naming
the feature), the adapter remembers it and retries once without that feature: the system text is merged into the user
message, and the reply is parsed by ``extract_json`` as usual. Nothing is invented: a failure is reported as a failure.
"""
from __future__ import annotations

import time
from typing import Any

import httpx

from ..config import Settings
from .base import LLMProvider, LLMResult, ProviderError, host_of, post_json

_BLOCK_REASONS = {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "RECITATION", "SPII"}
_SYSTEM_WORDS = ("developer instruction", "system instruction", "systeminstruction", "system_instruction")
_JSON_WORDS = ("json mode", "mime type", "mime_type", "responsemimetype", "response_mime_type", "application/json")


def _find_texts(node: Any, out: list[str]) -> None:
    """Collect answer text from response parts. Parts flagged as model 'thoughts' are never part of the answer."""
    if isinstance(node, dict):
        if node.get("thought") is True:
            return
        if isinstance(node.get("text"), str):
            out.append(node["text"])
            return
        for v in node.values():
            _find_texts(v, out)
    elif isinstance(node, list):
        for v in node:
            _find_texts(v, out)


class GemmaTextProvider(LLMProvider):
    name = "gemma"
    label = "Gemma (Google open model)"

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self.s = settings
        self._client = client or httpx.AsyncClient()
        self.model = settings.gemma_model
        self.price_in_per_mtok = settings.gemma_price_in_per_mtok
        self.price_out_per_mtok = settings.gemma_price_out_per_mtok
        self._no_system = False
        self._no_json = False

    @property
    def endpoint_host(self) -> str:
        return host_of(self.s.gemma_api_base)

    def configured(self) -> tuple[bool, str | None]:
        if not self.s.gemma_api_key:
            return False, ("Gemma is not configured (set GEMMA_API_KEY in backend/.env; a Google AI Studio key works, "
                           "get one at aistudio.google.com/apikey).")
        return True, None

    def _headers(self) -> dict[str, str]:
        return {"x-goog-api-key": self.s.gemma_api_key or "", "Content-Type": "application/json"}

    def _check_blocked(self, data: dict) -> None:
        fb = data.get("promptFeedback") or {}
        if fb.get("blockReason"):
            raise ProviderError(self.name, "blocked",
                                f"Gemma blocked the prompt ({fb['blockReason']}). Rephrase the brief or scene.")
        for cand in data.get("candidates") or []:
            reason = cand.get("finishReason")
            if reason in _BLOCK_REASONS:
                raise ProviderError(self.name, "blocked", f"Gemma stopped the response for safety reasons ({reason}).")

    async def complete(self, system: str, user: str, *, json_mode: bool, max_tokens: int,
                       temperature: float, timeout: float) -> LLMResult:
        ok, why = self.configured()
        if not ok:
            raise ProviderError(self.name, "missing_credentials", why or "not configured")
        for _ in range(3):
            try:
                return await self._call(system, user, json_mode and not self._no_json, max_tokens, temperature, timeout)
            except ProviderError as exc:
                low = exc.message.lower()
                if exc.http_status == 400 and not self._no_system and any(w in low for w in _SYSTEM_WORDS):
                    self._no_system = True
                    continue
                if exc.http_status == 400 and json_mode and not self._no_json and any(w in low for w in _JSON_WORDS):
                    self._no_json = True
                    continue
                raise
        raise ProviderError(self.name, "bad_request", "Gemma rejected the request format.")

    async def _call(self, system: str, user: str, use_json: bool, max_tokens: int, temperature: float,
                    timeout: float) -> LLMResult:
        url = f"{self.s.gemma_api_base}/models/{self.model}:generateContent"
        text_in = f"{system}\n\n{user}" if self._no_system else user
        payload: dict[str, Any] = {
            "contents": [{"role": "user", "parts": [{"text": text_in}]}],
            "generationConfig": {"maxOutputTokens": max_tokens, "temperature": temperature},
        }
        if not self._no_system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        if use_json:
            payload["generationConfig"]["responseMimeType"] = "application/json"
        started = time.perf_counter()
        data, _resp = await post_json(self._client, self.name, self.label, url, self._headers(), payload, timeout)
        latency = round((time.perf_counter() - started) * 1000)
        self._check_blocked(data)

        texts: list[str] = []
        for cand in (data.get("candidates") or [])[:1]:
            if cand.get("finishReason") == "MAX_TOKENS":
                raise ProviderError(self.name, "truncated", "Gemma stopped before finishing (token limit).",
                                    retryable=True)
            _find_texts((cand.get("content") or {}).get("parts"), texts)
        text = "".join(texts).strip()
        if not text:
            raise ProviderError(self.name, "bad_response", "Gemma returned no text.", retryable=True)

        u = data.get("usageMetadata") or {}
        in_tok = u.get("promptTokenCount")
        out_tok = u.get("candidatesTokenCount")
        if isinstance(out_tok, int) and isinstance(u.get("thoughtsTokenCount"), int):
            out_tok += u["thoughtsTokenCount"]
        return LLMResult(
            text=text, provider=self.name, model=str(data.get("modelVersion") or self.model),
            input_tokens=in_tok if isinstance(in_tok, int) else None,
            output_tokens=out_tok if isinstance(out_tok, int) else None,
            latency_ms=latency, request_id=str(data.get("responseId") or "") or None,
            endpoint_host=self.endpoint_host)

    async def aclose(self) -> None:
        await self._client.aclose()
