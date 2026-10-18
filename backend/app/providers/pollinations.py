"""Pollinations image adapter (ALL image generation in VYRA goes through this file).

Uses the Pollinations text-to-image endpoint:

    GET {POLLINATIONS_BASE_URL}/image/{prompt}?model=flux&width=..&height=..&seed=..&nologo=true
    Authorization: Bearer <POLLINATIONS_API_KEY>

The reply body is the image itself (JPEG/PNG/WebP). Errors come back as JSON with an HTTP error code and
are mapped to the same stable error codes the rest of the app already understands.

A product reference photo cannot be sent: Pollinations only accepts a *public URL* for reference images
and VYRA keeps uploaded photos private on disk. ``supports_reference`` is therefore False, and the image
prompt builder is told not to claim there is an "attached reference image".
"""
from __future__ import annotations

import random
import time
from urllib.parse import quote

import httpx

from ..config import Settings
from .base import ImageProvider, ImageResult, ProviderError, host_of, map_http_error

# Long side of the generated image in pixels (short side follows the aspect ratio).
LONG_SIDE = 1024
# Prompts travel in the URL path, so keep them comfortably below common URL length limits.
MAX_PROMPT_CHARS = 1500


def _size_for(aspect_ratio: str) -> tuple[int, int]:
    try:
        a, b = (float(x) for x in aspect_ratio.split(":", 1))
        if a <= 0 or b <= 0:
            raise ValueError
    except ValueError:
        a, b = 4.0, 3.0
    if a >= b:
        w, h = LONG_SIDE, LONG_SIDE * b / a
    else:
        w, h = LONG_SIDE * a / b, LONG_SIDE
    return int(round(w / 16) * 16), int(round(h / 16) * 16)


def _shrink(prompt: str) -> str:
    """Keep the start (the scene) and the end (the safety/no-text guards) when a prompt is too long."""
    prompt = " ".join(prompt.split())
    if len(prompt) <= MAX_PROMPT_CHARS:
        return prompt
    head = int(MAX_PROMPT_CHARS * 0.6)
    tail = MAX_PROMPT_CHARS - head - 5
    return prompt[:head].rstrip() + " ... " + prompt[-tail:].lstrip()


def _sniff(data: bytes) -> str | None:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


class PollinationsImageProvider(ImageProvider):
    name = "pollinations"
    label = "Pollinations"
    supports_reference = False

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self.s = settings
        self.model = settings.pollinations_model
        self._client = client or httpx.AsyncClient()

    @property
    def endpoint_host(self) -> str:
        return host_of(self.s.pollinations_base_url)

    def configured(self) -> tuple[bool, str | None]:
        if not self.s.pollinations_api_key:
            return False, "Pollinations is not configured (set POLLINATIONS_API_KEY in backend/.env)."
        return True, None

    async def generate(self, prompt: str, aspect_ratio: str, reference: tuple[str, bytes] | None,
                       timeout: float) -> ImageResult:
        ok, why = self.configured()
        if not ok:
            raise ProviderError(self.name, "missing_credentials", why or "not configured")
        width, height = _size_for(aspect_ratio)
        url = f"{self.s.pollinations_base_url}/image/{quote(_shrink(prompt), safe='')}"
        params = {"model": self.model, "width": width, "height": height, "nologo": "true",
                  "seed": random.randint(1, 2_000_000_000)}
        headers = {"Authorization": f"Bearer {self.s.pollinations_api_key}", "Accept": "image/*"}
        started = time.perf_counter()
        try:
            resp = await self._client.get(url, params=params, headers=headers,
                                          timeout=httpx.Timeout(timeout, connect=15.0), follow_redirects=True)
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
        data = resp.content
        mime = _sniff(data)
        if not mime:
            raise ProviderError(self.name, "bad_response", f"{self.label} did not return an image.", retryable=True)
        return ImageResult(data=data, mime=mime, provider=self.name, model=self.model, latency_ms=latency,
                           request_id=resp.headers.get("x-request-id"), endpoint_host=self.endpoint_host,
                           cost_usd=self.s.pollinations_image_cost_usd)

    async def aclose(self) -> None:
        await self._client.aclose()
