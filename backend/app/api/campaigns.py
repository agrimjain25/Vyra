"""Campaign CRUD, product reference image, generation and review jobs."""
from __future__ import annotations

from starlette.requests import Request

from ..errors import ApiError, bad_request, not_found
from ..framework import Router
from ..schemas import CampaignIn, GenerateIn, ReviewIn
from ..services.images import SCENE_ASPECT
from ..services.media import sniff
from ..services.story import story_stats

router = Router()


def st(request: Request):
    return request.app.state.vyra


def campaign_detail(state, cid: str) -> dict | None:
    repo = state.repo
    core = repo.get_campaign_core(cid)
    if not core:
        return None
    core.pop("product_image", None)
    audiences = repo.list_audiences(cid)
    images = repo.list_images_for_campaign(cid)
    calls = state.db.query(
        """SELECT experience_id, SUM(COALESCE(input_tokens,0)) i, SUM(COALESCE(output_tokens,0)) o, SUM(cost_usd) c,
           SUM(CASE WHEN ok=1 AND cost_usd IS NULL THEN 1 ELSE 0 END) unpriced FROM provider_calls
           WHERE campaign_id = ? GROUP BY experience_id""", (cid,))
    by_exp = {r["experience_id"]: r for r in calls}
    exps = []
    by_aud = {a["id"]: a for a in audiences}
    for e in repo.list_experiences(cid):
        mine = [i for i in images if i["experience_id"] == e["id"]]
        hero = [i for i in mine if i["scene_key"] == "hero"]
        e["audience"] = {k: by_aud[e["audience_id"]][k] for k in ("id", "name", "angle", "interests", "needs")}
        e["images"] = mine
        e["hero_url"] = next((i["url"] for i in reversed(hero) if i["status"] == "succeeded"), None)
        e["scene_image_urls"] = {i["scene_key"]: i["url"] for i in mine
                                 if i["status"] == "succeeded" and i["scene_key"] != "hero" and i["aspect_ratio"] == SCENE_ASPECT}
        e["story"] = story_stats(e["scenes"])
        c = by_exp.get(e["id"])
        e["cost"] = ({"usd": round(c["c"], 6) if c["c"] is not None and not c["unpriced"] else None,
                      "complete": not c["unpriced"] and c["c"] is not None,
                      "input_tokens": c["i"], "output_tokens": c["o"]} if c else None)
        exps.append(e)
    shared = by_exp.get(None)
    total_c = state.db.one(
        """SELECT SUM(cost_usd) c, SUM(COALESCE(input_tokens,0)) i, SUM(COALESCE(output_tokens,0)) o, COUNT(*) n,
           SUM(CASE WHEN ok=1 AND cost_usd IS NULL THEN 1 ELSE 0 END) unpriced FROM provider_calls
           WHERE campaign_id = ?""", (cid,))
    core["audiences"] = audiences
    core["experiences"] = exps
    core["active_jobs"] = repo.active_jobs(cid)
    core["tone_check"] = state.generator.evolus_blocks.summary(cid)  # None unless EVOLUS_SENTIMENT=true
    core["usage"] = {"calls": total_c["n"], "input_tokens": total_c["i"] or 0, "output_tokens": total_c["o"] or 0,
                     "cost_usd": round(total_c["c"], 6) if total_c["c"] is not None and not total_c["unpriced"] else None,
                     "cost_complete": bool(total_c["n"]) and not total_c["unpriced"] and total_c["c"] is not None,
                     "shared_calls_cost_note": "Includes the shared strategy and review calls." if shared else None}
    return core


def require_campaign(request: Request, cid: str) -> dict:
    d = campaign_detail(st(request), cid)
    if not d:
        raise not_found("Campaign")
    return d


@router.get("/api/campaigns", summary="List campaigns", tags=["Campaigns"])
async def list_campaigns(request: Request):
    return {"campaigns": st(request).repo.list_campaigns()}


@router.post("/api/campaigns", summary="Create a campaign brief", tags=["Campaigns"], body=CampaignIn, status=201)
async def create_campaign(request: Request, body: CampaignIn):
    state = st(request)
    cid = state.repo.create_campaign(body.model_dump())
    return campaign_detail(state, cid)


@router.get("/api/campaigns/{cid}", summary="Get a campaign with experiences, images and usage", tags=["Campaigns"])
async def get_campaign(request: Request):
    return require_campaign(request, request.path_params["cid"])


@router.put("/api/campaigns/{cid}", summary="Edit a campaign brief (experiences become stale if content changed)",
            tags=["Campaigns"], body=CampaignIn)
async def update_campaign(request: Request, body: CampaignIn):
    state = st(request)
    cid = request.path_params["cid"]
    require_campaign(request, cid)
    known = {a["id"] for a in state.repo.list_audiences(cid)}
    data = body.model_dump()
    for a in data["audiences"]:
        if a.get("id") and a["id"] not in known:
            raise bad_request("An audience id does not belong to this campaign.", "unknown_audience")
    stale = state.repo.update_campaign(cid, data)
    return {"campaign": campaign_detail(state, cid), "stale": stale}


@router.delete("/api/campaigns/{cid}", summary="Delete a campaign and its images", tags=["Campaigns"])
async def delete_campaign(request: Request):
    state = st(request)
    cid = request.path_params["cid"]
    require_campaign(request, cid)
    if state.repo.active_jobs(cid):
        raise ApiError(409, "job_running", "Wait for the running generation to finish before deleting.")
    files = state.repo.delete_campaign(cid)
    for f in files:
        state.media.delete(f)
    return {"deleted": True}


@router.post("/api/campaigns/{cid}/duplicate", summary="Duplicate the brief (no generated content)", tags=["Campaigns"], status=201)
async def duplicate_campaign(request: Request):
    state = st(request)
    require_campaign(request, request.path_params["cid"])
    new = state.repo.duplicate_campaign(request.path_params["cid"])
    return campaign_detail(state, new)


@router.post("/api/campaigns/{cid}/product-image", summary="Upload an optional product reference image (PNG/JPEG/WebP, max 5 MB)",
             tags=["Campaigns"], multipart=True)
async def upload_product_image(request: Request):
    state = st(request)
    cid = request.path_params["cid"]
    require_campaign(request, cid)
    form = await request.form()
    up = form.get("file")
    if up is None or not hasattr(up, "read"):
        raise bad_request("Send the image as multipart form field 'file'.", "missing_file")
    data = await up.read(state.settings.max_upload_bytes + 1)
    if len(data) > state.settings.max_upload_bytes:
        raise ApiError(413, "payload_too_large", f"The image must be {state.settings.max_upload_bytes // (1024 * 1024)} MB or smaller.")
    if sniff(data) is None:
        raise ApiError(415, "unsupported_media_type", "Only PNG, JPEG or WebP images are accepted.")
    saved = state.media.save(data)
    old = state.repo.set_product_image(cid, saved["file_name"])
    state.media.delete(old)
    return {"product_image_url": f"/media/{saved['file_name']}"}


@router.delete("/api/campaigns/{cid}/product-image", summary="Remove the product reference image", tags=["Campaigns"])
async def delete_product_image(request: Request):
    state = st(request)
    cid = request.path_params["cid"]
    require_campaign(request, cid)
    state.media.delete(state.repo.set_product_image(cid, None))
    return {"deleted": True}


def ensure_provider(state, purpose: str = "generation") -> None:
    chain = state.hub.chain(purpose)
    if not chain:
        raise ApiError(503, "no_provider_selected", "No AI provider is selected. Set PRIMARY_LLM_PROVIDER in backend/.env.")
    reasons = []
    for p in chain:
        ok, why = p.configured()
        if ok:
            return
        reasons.append(why)
    raise ApiError(503, "no_provider_configured", " ".join(dict.fromkeys(r for r in reasons if r)),
                   {"how_to_fix": "Open backend/.env, fill in the key(s) shown above, then restart the backend."})


@router.post("/api/campaigns/{cid}/generate", summary="Generate content for all (or some) audiences as a background job",
             tags=["Generation"], body=GenerateIn, status=202, expensive=True)
async def generate(request: Request, body: GenerateIn):
    state = st(request)
    cid = request.path_params["cid"]
    require_campaign(request, cid)
    ensure_provider(state)
    ids = None
    if body.audience_ids:
        known = {a["id"] for a in state.repo.list_audiences(cid)}
        if not set(body.audience_ids) <= known:
            raise bad_request("Unknown audience id.", "unknown_audience")
        ids = body.audience_ids
    job, started = state.jobs.submit(
        "generate_campaign", f"gen:{cid}",
        lambda progress: state.generator.generate_campaign(cid, ids, body.review, progress), campaign_id=cid)
    return {"job": job, "already_running": not started}


@router.post("/api/campaigns/{cid}/review", summary="Re-run the brand-safety review (deterministic + optional AI)",
             tags=["Safety"], body=ReviewIn, status=202, expensive=True)
async def review(request: Request, body: ReviewIn):
    state = st(request)
    cid = request.path_params["cid"]
    require_campaign(request, cid)
    if body.ai:
        ensure_provider(state, "review")

    async def run(progress):
        progress(step="review", message="Running brand-safety review…")
        r = await state.generator.review_campaign(cid, use_ai=body.ai)
        return {"counts": r["counts"]}

    job, started = state.jobs.submit("review", f"review:{cid}", run, campaign_id=cid)
    return {"job": job, "already_running": not started}
