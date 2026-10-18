"""Provider contracts shared by every AI adapter."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx


class ProviderError(Exception):
    """A provider failure with a stable code, safe to show to the user (never contains secrets)."""

    def __init__(self, provider: str, code: str, message: str, *, retryable: bool = False,
                 http_status: int | None = None, retry_after: float | None = None):
        super().__init__(message)
        self.provider = provider
        self.code = code
        self.message = message
        self.retryable = retryable
        self.http_status = http_status
        self.retry_after = retry_after

    def as_dict(self) -> dict:
        return {"provider": self.provider, "code": self.code, "message": self.message,
                "retryable": self.retryable, "http_status": self.http_status}


@dataclass
class LLMResult:
    text: str
    provider: str
    model: str
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: int
    request_id: str | None
    endpoint_host: str


@dataclass
class ImageResult:
    data: bytes
    mime: str
    provider: str
    model: str
    latency_ms: int
    request_id: str | None
    endpoint_host: str
    cost_usd: float | None


class LLMProvider:
    name = "base"
    label = "Base"
    model = ""
    price_in_per_mtok: float | None = None
    price_out_per_mtok: float | None = None

    def configured(self) -> tuple[bool, str | None]:
        raise NotImplementedError

    @property
    def endpoint_host(self) -> str:
        return ""

    async def complete(self, system: str, user: str, *, json_mode: bool, max_tokens: int,
                       temperature: float, timeout: float) -> LLMResult:
        raise NotImplementedError

    def cost(self, input_tokens: int | None, output_tokens: int | None) -> float | None:
        """Estimated USD, only when both token counts and a configured price exist."""
        if input_tokens is None or output_tokens is None:
            return None
        if self.price_in_per_mtok is None or self.price_out_per_mtok is None:
            return None
        return round(input_tokens / 1e6 * self.price_in_per_mtok + output_tokens / 1e6 * self.price_out_per_mtok, 6)

    async def aclose(self) -> None:
        pass


class ImageProvider:
    name = "base"
    label = "Base"
    model = ""
    supports_reference = False  # can a product reference photo be sent along?

    def configured(self) -> tuple[bool, str | None]:
        raise NotImplementedError

    @property
    def endpoint_host(self) -> str:
        return ""

    async def generate(self, prompt: str, aspect_ratio: str, reference: tuple[str, bytes] | None,
                       timeout: float) -> ImageResult:
        raise NotImplementedError

    async def aclose(self) -> None:
        pass


# --------------------------------------------------------------------------- shared HTTP helpers

def host_of(url: str | None) -> str:
    return urlparse(url or "").netloc or ""


def is_loopback(host: str) -> bool:
    h = host.split(":")[0].lower()
    return h in ("localhost", "127.0.0.1", "::1", "[::1]", "0.0.0.0") or h.startswith("127.")


def _error_message(resp: httpx.Response) -> str:
    try:
        data = resp.json()
    except ValueError:
        return (resp.text or "").strip()[:240]
    if isinstance(data, dict):
        err = data.get("error", data)
        if isinstance(err, dict):
            msg = err.get("message") or err.get("detail") or err.get("status")
            if msg:
                return str(msg)[:240]
        elif isinstance(err, str):
            return err[:240]
        if isinstance(data.get("detail"), str):
            return data["detail"][:240]
        if isinstance(data.get("message"), str):
            return data["message"][:240]
    return json.dumps(data)[:240]


_QUOTA = re.compile(r"quota|billing|insufficient|credit|exceeded your|balance|payment", re.I)
_BLOCK = re.compile(r"safety|blocked|prohibited|policy|harmful|not allowed", re.I)
_BADKEY = re.compile(r"api key|api_key|invalid key|unauthenticated|permission", re.I)


def map_http_error(provider: str, label: str, resp: httpx.Response) -> ProviderError:
    status = resp.status_code
    msg = _error_message(resp)
    retry_after = None
    ra = resp.headers.get("retry-after")
    if ra and ra.replace(".", "", 1).isdigit():
        retry_after = min(float(ra), 20.0)
    if status in (401, 403) or (status == 400 and _BADKEY.search(msg) and "key" in msg.lower()):
        return ProviderError(provider, "auth_failed",
                             f"{label} rejected the API key or permissions ({status}). Check the key in backend/.env. {msg}".strip(),
                             http_status=status)
    if status == 402 or (status == 429 and _QUOTA.search(msg)):
        return ProviderError(provider, "quota_exhausted",
                             f"{label} says the quota or credits are used up. {msg}".strip(), http_status=status)
    if status == 429:
        return ProviderError(provider, "rate_limited", f"{label} is rate limiting requests. {msg}".strip(),
                             retryable=True, http_status=status, retry_after=retry_after)
    if status == 404:
        return ProviderError(provider, "unsupported_model",
                             f"{label} could not find that model or endpoint (404). Check the model name and base URL. {msg}".strip(),
                             http_status=status)
    if status in (408, 409, 425) or status >= 500:
        return ProviderError(provider, "provider_unavailable", f"{label} is temporarily unavailable ({status}). {msg}".strip(),
                             retryable=True, http_status=status, retry_after=retry_after)
    if status == 400 and _BLOCK.search(msg):
        return ProviderError(provider, "blocked", f"{label} blocked the request. {msg}".strip(), http_status=status)
    return ProviderError(provider, "bad_request", f"{label} rejected the request ({status}). {msg}".strip(),
                         http_status=status)


async def post_json(client: httpx.AsyncClient, provider: str, label: str, url: str, headers: dict[str, str],
                    payload: dict[str, Any], timeout: float) -> tuple[dict, httpx.Response]:
    try:
        resp = await client.post(url, headers=headers, json=payload, timeout=httpx.Timeout(timeout, connect=15.0))
    except httpx.TimeoutException:
        raise ProviderError(provider, "timeout", f"{label} did not answer within {int(timeout)} seconds.",
                            retryable=True) from None
    except httpx.TransportError as exc:
        raise ProviderError(provider, "unreachable",
                            f"Could not reach {label} ({type(exc).__name__}). Check the base URL and your network.",
                            retryable=True) from None
    if resp.status_code >= 400:
        raise map_http_error(provider, label, resp)
    try:
        data = resp.json()
    except ValueError:
        raise ProviderError(provider, "bad_response", f"{label} returned something that is not JSON.",
                            retryable=True) from None
    if not isinstance(data, dict):
        raise ProviderError(provider, "bad_response", f"{label} returned an unexpected response shape.", retryable=True)
    return data, resp


# --------------------------------------------------------------------------- JSON extraction

_THINK = re.compile(r"<think>.*?</think>", re.S | re.I)
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.I)


def extract_json(text: str) -> Any:
    """Pull the first JSON object out of a model reply (handles code fences and reasoning tags)."""
    cleaned = _THINK.sub("", text or "").strip()
    cleaned = _FENCE.sub("", cleaned).strip()
    start = cleaned.find("{")
    if start < 0:
        raise ValueError("no JSON object found in the reply")
    try:
        obj, _ = json.JSONDecoder().raw_decode(cleaned[start:])
    except json.JSONDecodeError as exc:
        raise ValueError(f"reply is not valid JSON ({exc.msg} at position {exc.pos})") from None
    if not isinstance(obj, dict):
        raise ValueError("expected a JSON object")
    return obj
