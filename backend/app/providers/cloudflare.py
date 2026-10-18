"""Cloudflare Workers AI image adapter (free daily allowance; FLUX.1-schnell by default).

    POST {CF_BASE_URL}/accounts/{CF_ACCOUNT_ID}/ai/run/{CF_IMAGE_MODEL}
    Authorization: Bearer <CF_API_TOKEN>
    {"prompt": "..", "steps": 4}

The reply is JSON: {"result": {"image": "<base64 JPEG>"}, "success": true, ...}.
FLUX.1-schnell on Workers AI does not take a width/height, so the aspect ratio is not sent (the app shows
the image inside the requested frame). A product reference photo cannot be sent either.
"""
from __future__ import annotations

import base64
import random
import time

import httpx

from ..config import Settings
from .base import ImageProvider, ImageResult, ProviderError, host_of, map_http_error
from .pollinations import _sniff

MAX_PROMPT_CHARS = 2000  # the model accepts 1-2048 characters


def _shrink(prompt: str) -> str:
    prompt = " ".join(prompt.split())
    if len(prompt) <= MAX_PROMPT_CHARS:
        return prompt
    head = int(MAX_PROMPT_CHARS * 0.6)
    tail = MAX_PROMPT_CHARS - head - 5
    return prompt[:head].rstrip() + " ... " + prompt[-tail:].lstrip()


class CloudflareImageProvider(ImageProvider):
    name = "cloudflare"
    label = "Cloudflare Workers AI"
    supports_reference = False

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self.s = settings
        self.model = settings.cf_image_model
        self._client = client or httpx.AsyncClient()

    @property
    def endpoint_host(self) -> str:
        return host_of(self.s.cf_base_url)

    def configured(self) -> tuple[bool, str | None]:
        if not self.s.cf_account_id or not self.s.cf_api_token:
            return False, "Cloudflare is not configured (set CF_ACCOUNT_ID and CF_API_TOKEN in backend/.env)."
        return True, None

    async def generate(self, prompt: str, aspect_ratio: str, reference: tuple[str, bytes] | None,
                       timeout: float) -> ImageResult:
        ok, why = self.configured()
        if not ok:
            raise ProviderError(self.name, "missing_credentials", why or "not configured")
        url = f"{self.s.cf_base_url}/accounts/{self.s.cf_account_id}/ai/run/{self.model}"
        payload = {"prompt": _shrink(prompt), "steps": 4}
        headers = {"Authorization": f"Bearer {self.s.cf_api_token}", "Content-Type": "application/json"}
        started = time.perf_counter()
        try:
            resp = await self._client.post(url, json=payload, headers=headers,
                                           timeout=httpx.Timeout(timeout, connect=15.0))
        except httpx.TimeoutException:
            raise ProviderError(self.name, "timeout", f"{self.label} did not answer within {int(timeout)} seconds.",
                                retryable=True) from None
        except httpx.TransportError as exc:
            raise ProviderError(self.name, "unreachable",
                                f"Could not reach {self.label} ({type(exc).__name__}). Check your network.",
                                retryable=True) from None
        latency = round((time.perf_counter() - started) * 1000)
        if resp.status_code >= 400:
            raise map_http_error(self.name, self.label, resp)
        try:
            body = resp.json()
            b64 = (body.get("result") or body)["image"]
            data = base64.b64decode(b64)
        except (ValueError, KeyError, TypeError, AttributeError):
            raise ProviderError(self.name, "bad_response", f"{self.label} did not return an image.",
                                retryable=True) from None
        mime = _sniff(data)
        if not mime:
            raise ProviderError(self.name, "bad_response", f"{self.label} did not return an image.", retryable=True)
        return ImageResult(data=data, mime=mime, provider=self.name, model=self.model, latency_ms=latency,
                           request_id=resp.headers.get("cf-ray"), endpoint_host=self.endpoint_host,
                           cost_usd=self.s.cf_image_cost_usd)

    async def aclose(self) -> None:
        await self._client.aclose()
