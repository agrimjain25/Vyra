"""VYRA backend application factory."""
from __future__ import annotations

import contextlib
import logging

from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.routing import Route

from . import __version__
from .api import campaigns, evolus_blocks, experiences, system
from .config import ConfigError, Settings
from .errors import ApiError
from .framework import SWAGGER_HTML, Router, build_openapi, validation_details
from .logging_setup import setup_logging
from .security import (BodyLimitMiddleware, RequestContextMiddleware, SecurityHeadersMiddleware,
                       TokenAuthMiddleware)
from .state import AppState, build_state

logger = logging.getLogger("vyra")
FRONTEND_FILES = {"/config.js": "public/config.js", "/favicon.svg": "public/favicon.svg",
                  "/icons.svg": "public/icons.svg"}


def create_app(settings: Settings | None = None) -> Starlette:
    settings = settings or Settings.from_env()
    setup_logging(settings.log_level, settings.secret_values())

    routers: list[Router] = [system.router, system.media_router, campaigns.router, experiences.router,
                             evolus_blocks.router]
    endpoints = [ep for r in routers for ep in r.endpoints]
    public = {(ep.method, ep.path) for ep in endpoints if ep.public}

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette):
        state: AppState = build_state(settings)
        app.state.vyra = state
        logger.info("VYRA backend %s started (primary=%s fallback=%s)", __version__, settings.primary_llm,
                    settings.fallback_llm or "none")
        try:
            yield
        finally:
            await state.jobs.shutdown()
            await state.hub.aclose()

    async def openapi(request: Request):
        spec = build_openapi([ep for ep in endpoints if ep.path.startswith("/api/")], "VYRA API", __version__,
                             "One campaign brief becomes distinct, interactive experiences for each audience. "
                             "Errors are JSON: {error:{code,message,details?}}. Long operations return a job; poll /api/jobs/{id}.")
        return JSONResponse(spec)

    async def docs(request: Request):
        return HTMLResponse(SWAGGER_HTML)

    async def index(request: Request):
        f = settings.frontend_dir / "index.html"
        if not f.is_file():
            return JSONResponse({"error": {"code": "frontend_missing",
                                           "message": "index.html was not found. See FRONTEND_DIR in backend/.env."}}, 404)
        return FileResponse(f, media_type="text/html", headers={"Cache-Control": "no-cache"})

    def static_file(rel: str, mime: str):
        async def handler(request: Request):
            f = settings.frontend_dir / rel
            if not f.is_file():
                return JSONResponse({"error": {"code": "not_found", "message": "Not found."}}, 404)
            return FileResponse(f, media_type=mime, headers={"Cache-Control": "no-cache"})
        return handler

    async def api_error(request: Request, exc: ApiError):
        return JSONResponse(exc.to_body(), status_code=exc.status)

    async def http_error(request: Request, exc: HTTPException):
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        msg = {404: "That address does not exist.", 405: "That method is not allowed here."}.get(exc.status_code, str(exc.detail))
        return JSONResponse({"error": {"code": code, "message": msg}}, status_code=exc.status_code)

    async def validation_error(request: Request, exc: ValidationError):
        d = validation_details(exc)
        return JSONResponse({"error": {"code": "validation_error", "message": f"{d[0]['field']}: {d[0]['message']}",
                                       "details": d}}, status_code=422)

    async def unexpected(request: Request, exc: Exception):
        rid = request.scope.get("state", {}).get("request_id")
        logger.exception("unhandled error (request %s)", rid)
        return JSONResponse({"error": {"code": "internal_error", "message": "Something went wrong on the server.",
                                       "request_id": rid}}, status_code=500)

    routes = [r for rt in routers for r in rt.routes()]
    routes += [Route("/api/openapi.json", openapi), Route("/api/docs", docs), Route("/", index),
               Route("/index.html", index)]
    for path, rel in FRONTEND_FILES.items():
        mime = "application/javascript" if path.endswith(".js") else "image/svg+xml"
        routes.append(Route(path, static_file(rel, mime)))

    middleware = [
        Middleware(RequestContextMiddleware),
        Middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                   allow_headers=["Content-Type", "X-Vyra-Token", "Authorization"], expose_headers=["X-Request-ID"]),
        Middleware(SecurityHeadersMiddleware),
        Middleware(BodyLimitMiddleware, max_bytes=settings.max_body_bytes),
        Middleware(TokenAuthMiddleware, token=settings.api_token),
    ]
    return Starlette(routes=routes, middleware=middleware, lifespan=lifespan, exception_handlers={
        ApiError: api_error, HTTPException: http_error, ValidationError: validation_error, Exception: unexpected})


def app_from_env() -> Starlette:
    try:
        return create_app()
    except ConfigError as exc:
        raise SystemExit(f"\nConfiguration problem: {exc}\n")


app = None  # created lazily by uvicorn via "app.main:app_from_env --factory"
