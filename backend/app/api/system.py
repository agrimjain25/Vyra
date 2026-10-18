"""Health, integrations, brand guide, jobs, analytics and media."""
from __future__ import annotations

from datetime import datetime, timezone

from starlette.requests import Request
from starlette.responses import FileResponse

from .. import __version__
from ..errors import ApiError, not_found
from ..framework import Router
from ..providers.base import ProviderError, extract_json
from ..schemas import BrandIn
from ..services import analytics as analytics_service
from ..services import integrations as integrations_service
from ..services.prompts import ping_prompt
from .campaigns import st

router = Router()
media_router = Router()


@router.get("/api/health", summary="Health check with provider and integration summary", tags=["System"], public=True)
async def health(request: Request):
    state = st(request)
    s = state.settings

    def prov(p):
        ok, why = p.configured()
        return {"name": p.name, "label": p.label, "configured": ok, "model": p.model or None,
                "endpoint_host": p.endpoint_host or None, "problem": why}

    img_ok, img_why = state.hub.image_ready()
    return {
        "status": "ok" if state.db.check() else "degraded",
        "version": __version__, "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "database": {"ok": state.db.check()},
        "auth_required": bool(s.api_token),
        "llm": {"primary": s.primary_llm, "fallback": s.fallback_llm or "none",
                "chain": [prov(p) for p in state.hub.chain("generation")]},
        "image": {"provider": state.hub.image.name, "model": state.hub.image.model, "configured": img_ok,
                  "problem": img_why,
                  "backup": state.hub.image_backup.name if state.hub.image_backup else None},
        "integrations": {i["id"]: i["state"] for i in integrations_service.build(s, state.repo, state.hub)},
    }


@router.get("/api/integrations", summary="Truthful status of every partner technology", tags=["System"])
async def integrations(request: Request):
    state = st(request)
    return {"integrations": integrations_service.build(state.settings, state.repo, state.hub),
            "states": integrations_service.STATES}


@router.post("/api/integrations/{iid}/test", summary="Make one tiny real call to verify a provider (costs a little)",
             tags=["System"], expensive=True)
async def test_integration(request: Request):
    state = st(request)
    iid = request.path_params["iid"]
    hub = state.hub
    if iid == "image_generation":
        prov = hub.image
        ok, why = prov.configured()
        if not ok:
            raise ApiError(503, "not_configured", why or "Not configured.")
        if state.settings.image_max_per_day and state.repo.count_image_attempts_since(24) >= state.settings.image_max_per_day:
            raise ApiError(429, "daily_cap", "The daily image limit was reached.")
        try:
            res = await prov.generate("A plain light-grey square with a single small green circle in the centre. "
                                      "No text.", "1:1", None, state.settings.image_timeout_s)
        except ProviderError as exc:
            state.repo.add_call(purpose="test", provider=prov.name, model=prov.model, endpoint_host=prov.endpoint_host,
                                ok=False, error_code=exc.code, error_message=exc.message)
            state.repo.add_call(purpose="image", provider=prov.name, model=prov.model, endpoint_host=prov.endpoint_host,
                                ok=False, error_code=exc.code, error_message=exc.message)
            return {"ok": False, "error": exc.as_dict()}
        state.repo.add_call(purpose="image", provider=res.provider, model=res.model, endpoint_host=res.endpoint_host,
                            ok=True, latency_ms=res.latency_ms, cost_usd=res.cost_usd, request_id=res.request_id)
        return {"ok": True, "latency_ms": res.latency_ms, "model": res.model, "bytes": len(res.data), "mime": res.mime}
    if iid == "evolus_tone":
        blocks = state.generator.evolus_blocks
        if not blocks.enabled:
            raise ApiError(409, "feature_off", "The Evolus Sentiment tool is switched off. Set EVOLUS_SENTIMENT=true in backend/.env.")
        ok, why = blocks.configured()
        if not ok:
            raise ApiError(503, "not_configured", why or "Not configured.")
        res = await blocks.run_test()
        if not res.get("ok"):
            return {"ok": False, "error": {"code": res.get("error_code"), "message": res.get("error_message")}}
        return {"ok": True, "latency_ms": res.get("latency_ms"), "model": blocks.tool, "endpoint_host": blocks.endpoint_host,
                "label": res.get("label"), "score": res.get("score")}
    names = {"amd": "amd", "gemma": "gemma", "evolus": "evolus"}
    if iid not in names:
        raise not_found("Integration")
    prov = hub.llms[names[iid]]
    ok, why = prov.configured()
    if not ok:
        raise ApiError(503, "not_configured", why or "Not configured.")
    system, user = ping_prompt()
    try:
        res = await prov.complete(system, user, json_mode=True, max_tokens=60, temperature=0, timeout=state.settings.llm_timeout_s)
        try:
            extract_json(res.text)
            parsed_ok = True
        except ValueError:
            parsed_ok = False
    except ProviderError as exc:
        state.repo.add_call(purpose="test", provider=prov.name, model=prov.model, endpoint_host=prov.endpoint_host,
                            ok=False, error_code=exc.code, error_message=exc.message)
        return {"ok": False, "error": exc.as_dict()}
    state.repo.add_call(purpose="test", provider=prov.name, model=res.model, endpoint_host=res.endpoint_host, ok=True,
                        latency_ms=res.latency_ms, input_tokens=res.input_tokens, output_tokens=res.output_tokens,
                        cost_usd=prov.cost(res.input_tokens, res.output_tokens), request_id=res.request_id)
    return {"ok": True, "latency_ms": res.latency_ms, "model": res.model, "input_tokens": res.input_tokens,
            "output_tokens": res.output_tokens, "returned_valid_json": parsed_ok, "request_id": res.request_id,
            "endpoint_host": res.endpoint_host}


@router.get("/api/brand", summary="Get the saved brand guide", tags=["Brand"])
async def get_brand(request: Request):
    return st(request).repo.get_brand()


@router.put("/api/brand", summary="Save the brand guide", tags=["Brand"], body=BrandIn)
async def put_brand(request: Request, body: BrandIn):
    if not body.name:
        raise ApiError(422, "validation_error", "name: Enter a brand name before saving.",
                       [{"field": "name", "message": "Enter a brand name before saving."}])
    return st(request).repo.put_brand(body.model_dump())


@router.delete("/api/brand", summary="Clear the brand guide", tags=["Brand"])
async def clear_brand(request: Request):
    st(request).repo.clear_brand()
    return {"cleared": True}


@router.get("/api/jobs/{jid}", summary="Poll a background job", tags=["Jobs"])
async def get_job(request: Request):
    job = st(request).repo.get_job(request.path_params["jid"])
    if not job:
        raise not_found("Job")
    return job


@router.get("/api/analytics/summary", summary="Counts, usage, cost and viewer events from real recorded data", tags=["Analytics"])
async def analytics(request: Request):
    return analytics_service.summary(st(request).repo)


@media_router.get("/media/{name}", summary="Serve a generated image", tags=["Media"], public=True)
async def media(request: Request):
    p = st(request).media.path(request.path_params["name"])
    if not p:
        raise not_found("Image")
    mime = {".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp"}[p.suffix]
    return FileResponse(p, media_type=mime, headers={"Cache-Control": "public, max-age=31536000, immutable",
                                                      "Content-Disposition": "inline"})
