"""Request-level protections: size limit, optional shared token, rate limiting, headers, request ids."""
from __future__ import annotations

import hmac
import json
import logging
import threading
import time
import uuid
from collections import defaultdict, deque

from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .errors import ApiError
from .logging_setup import log

logger = logging.getLogger("vyra.http")


def _json_bytes(status_code: int, code: str, message: str) -> tuple[list, bytes]:
    body = json.dumps({"error": {"code": code, "message": message}}).encode()
    headers = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]
    return headers, body


async def _send_error(send: Send, status: int, code: str, message: str) -> None:
    headers, body = _json_bytes(status, code, message)
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


class _TooLarge(Exception):
    pass


class BodyLimitMiddleware:
    """Rejects bodies larger than ``max_bytes`` (declared or streamed)."""

    def __init__(self, app: ASGIApp, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers") or [])
        declared = headers.get(b"content-length")
        if declared and declared.isdigit() and int(declared) > self.max_bytes:
            return await _send_error(send, 413, "payload_too_large", "Request body is too large.")
        received = 0
        started = False

        async def limited_receive() -> Message:
            nonlocal received
            msg = await receive()
            if msg["type"] == "http.request":
                received += len(msg.get("body", b""))
                if received > self.max_bytes:
                    raise _TooLarge()
            return msg

        async def tracking_send(msg: Message) -> None:
            nonlocal started
            if msg["type"] == "http.response.start":
                started = True
            await send(msg)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except _TooLarge:
            if not started:
                await _send_error(send, 413, "payload_too_large", "Request body is too large.")


class TokenAuthMiddleware:
    """Optional shared-token protection for /api/*. Disabled when no token is configured."""

    EXEMPT = ("/api/health", "/api/openapi.json", "/api/docs")

    def __init__(self, app: ASGIApp, token: str | None):
        self.app = app
        self.token = token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self.token:
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        if scope.get("method") == "OPTIONS" or not path.startswith("/api/") or path in self.EXEMPT:
            return await self.app(scope, receive, send)
        headers = {k.lower(): v for k, v in (scope.get("headers") or [])}
        supplied = headers.get(b"x-vyra-token", b"").decode("latin-1")
        if not supplied:
            auth = headers.get(b"authorization", b"").decode("latin-1")
            if auth.lower().startswith("bearer "):
                supplied = auth[7:].strip()
        if not hmac.compare_digest(supplied.encode(), self.token.encode()):
            return await _send_error(send, 401, "unauthorized",
                                     "This VYRA server needs an access token. Add it under Settings.")
        await self.app(scope, receive, send)


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path", "")

        async def wrapped(msg: Message) -> None:
            if msg["type"] == "http.response.start":
                headers = list(msg.get("headers") or [])
                headers.append((b"x-content-type-options", b"nosniff"))
                headers.append((b"referrer-policy", b"no-referrer"))
                headers.append((b"x-frame-options", b"SAMEORIGIN"))
                if path.startswith("/api/") and path not in ("/api/docs", "/api/openapi.json"):
                    headers.append((b"cache-control", b"no-store"))
                msg = {**msg, "headers": headers}
            await send(msg)

        await self.app(scope, receive, wrapped)


class RequestContextMiddleware:
    """Adds a request id and writes one structured access-log line per request (path only, no query)."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        rid = uuid.uuid4().hex[:12]
        scope.setdefault("state", {})["request_id"] = rid
        start = time.perf_counter()
        status = 0

        async def wrapped(msg: Message) -> None:
            nonlocal status
            if msg["type"] == "http.response.start":
                status = msg["status"]
                msg = {**msg, "headers": list(msg.get("headers") or []) + [(b"x-request-id", rid.encode())]}
            await send(msg)

        try:
            await self.app(scope, receive, wrapped)
        finally:
            path = scope.get("path", "")
            if not path.startswith("/media/") or status >= 400:
                log(logger, logging.INFO, "request", request_id=rid, method=scope.get("method"), path=path,
                    status=status, ms=round((time.perf_counter() - start) * 1000))


class RateLimiter:
    """In-memory sliding-window limiter per client address for expensive (paid) endpoints."""

    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, request: Request, cost: int = 1) -> None:
        if self.per_minute <= 0:
            return
        client = request.client.host if request.client else "unknown"
        now = time.monotonic()
        with self._lock:
            dq = self._hits[client]
            while dq and now - dq[0] > 60:
                dq.popleft()
            if len(dq) + cost > self.per_minute:
                retry = max(1, int(60 - (now - dq[0]))) if dq else 60
                raise ApiError(429, "rate_limited",
                               f"Too many generation requests. Try again in about {retry} seconds.",
                               {"retry_after_seconds": retry})
            for _ in range(cost):
                dq.append(now)
