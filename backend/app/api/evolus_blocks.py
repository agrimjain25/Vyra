"""Evolus building block endpoints (AI Tool: Sentiment) and the AMD proof summary. All additive and optional."""
from __future__ import annotations

from starlette.requests import Request

from ..errors import ApiError
from ..framework import Router
from ..services import amd_proof
from .campaigns import require_campaign, st

router = Router()


@router.get("/api/campaigns/{cid}/tone-check", summary="Saved Evolus Sentiment (AI Tool) results for a campaign",
            tags=["Evolus"])
async def get_tone_check(request: Request):
    state = st(request)
    require_campaign(request, request.path_params["cid"])
    blocks = state.generator.evolus_blocks
    summary = blocks.summary(request.path_params["cid"])
    if summary is None:
        return {"enabled": False, "message": "The Evolus Sentiment tool is switched off. Set EVOLUS_SENTIMENT=true in backend/.env."}
    return summary


@router.post("/api/campaigns/{cid}/tone-check", summary="Run the Evolus Sentiment AI Tool on every audience (informational only)",
             tags=["Evolus"], expensive=True)
async def run_tone_check(request: Request):
    state = st(request)
    cid = request.path_params["cid"]
    require_campaign(request, cid)
    blocks = state.generator.evolus_blocks
    if not blocks.enabled:
        raise ApiError(409, "feature_off", "The Evolus Sentiment tool is switched off. Set EVOLUS_SENTIMENT=true in backend/.env and restart.")
    ok, why = blocks.configured()
    if not ok:
        raise ApiError(503, "not_configured", why or "Evolus is not configured.")
    return await blocks.run_for_campaign(cid)


@router.get("/api/proof/amd", summary="What really ran on the AMD endpoint, from recorded calls (for judges)", tags=["AMD"])
async def amd_proof_summary(request: Request):
    state = st(request)
    return amd_proof.summarize(state.repo, state.settings)
