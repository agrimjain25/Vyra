"""Experiences: text edits, regeneration, the interactive story player, images and viewer events."""
from __future__ import annotations

import asyncio

from starlette.requests import Request

from ..errors import ApiError, bad_request, not_found
from ..framework import Router
from ..providers.hub import GenerationFailed
from ..schemas import EventIn, ExperiencePatch, ImageGenIn, PlayIn, TranslateIn
from ..services.images import SCENE_ASPECT
from ..services.story import next_scene, public_scene
from ..services.translate import with_stale
from .campaigns import ensure_provider, st

router = Router()


def _exp(request: Request, eid: str) -> dict:
    e = st(request).repo.get_experience(eid)
    if not e:
        raise not_found("Experience")
    return e


@router.get("/api/experiences/{eid}", summary="Get one experience with its images", tags=["Experiences"])
async def get_experience(request: Request):
    state = st(request)
    e = _exp(request, request.path_params["eid"])
    e["images"] = state.repo.list_images(e["id"])
    aud = next(a for a in state.repo.list_audiences(e["campaign_id"]) if a["id"] == e["audience_id"])
    e["audience"] = aud
    return e


@router.patch("/api/experiences/{eid}", summary="Human edit of headline, copy or CTA (re-runs the fixed-rule review)",
              tags=["Experiences"], body=ExperiencePatch)
async def patch_experience(request: Request, body: ExperiencePatch):
    state = st(request)
    e = _exp(request, request.path_params["eid"])
    if e["status"] not in ("ready", "stale"):
        raise ApiError(409, "experience_not_ready", "Only generated experiences can be edited.")
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    state.repo.patch_experience_text(e["id"], fields)
    state.generator.rerun_deterministic(e["campaign_id"])
    return state.repo.get_experience(e["id"])


@router.post("/api/experiences/{eid}/regenerate", summary="Regenerate one audience's experience (background job)",
             tags=["Generation"], status=202, expensive=True)
async def regenerate(request: Request):
    state = st(request)
    e = _exp(request, request.path_params["eid"])
    ensure_provider(state)
    cid = e["campaign_id"]
    aid = e["audience_id"]
    job, started = state.jobs.submit(
        "generate_campaign", f"gen:{cid}",
        lambda progress: state.generator.generate_campaign(cid, [aid], True, progress),
        campaign_id=cid, experience_id=e["id"])
    return {"job": job, "already_running": not started}


@router.post("/api/experiences/{eid}/translate",
             summary="Translate one experience into other languages (Gemma first, Evolus agent as fallback)",
             tags=["Translation"], body=TranslateIn, expensive=True)
async def translate(request: Request, body: TranslateIn):
    state = st(request)
    e = _exp(request, request.path_params["eid"])
    if e["status"] not in ("ready", "stale"):
        raise ApiError(409, "experience_not_ready", "Only generated experiences can be translated.")
    ensure_provider(state, "translate")
    campaign = state.repo.get_campaign_core(e["campaign_id"])
    langs = [lang for lang in body.languages if lang.lower() != campaign["language"].lower()]
    if not langs:
        raise bad_request(f"This experience is already written in {campaign['language']}. Choose another language.",
                          "same_language")

    async def one(lang: str):
        try:
            return {"language": lang, "ok": True, "translation": await state.translator.translate(campaign, e, lang)}
        except GenerationFailed as exc:
            return {"language": lang, "ok": False, "error": exc.as_dict()}

    results = await asyncio.gather(*(one(lang) for lang in langs))
    if not any(r["ok"] for r in results):
        first = results[0]["error"]
        raise ApiError(502, first.get("code", "translation_failed"), first.get("message", "Translation failed."),
                       first.get("attempts"))
    return {"results": [{**r, "translation": with_stale(e, [r["translation"]])[0]} if r["ok"] else r for r in results]}


@router.get("/api/experiences/{eid}/translations", summary="List saved translations of one experience",
            tags=["Translation"])
async def list_translations(request: Request):
    state = st(request)
    e = _exp(request, request.path_params["eid"])
    return {"translations": with_stale(e, state.repo.list_translations(e["id"]))}


@router.get("/api/campaigns/{cid}/translations", summary="All saved translations of a campaign, by experience",
            tags=["Translation"])
async def campaign_translations(request: Request):
    state = st(request)
    cid = request.path_params["cid"]
    if not state.repo.get_campaign_core(cid):
        raise not_found("Campaign")
    by_exp = state.repo.list_translations_for_campaign(cid)
    exps = {e["id"]: e for e in state.repo.list_experiences(cid)}
    return {"translations": {eid: with_stale(exps[eid], ts) for eid, ts in by_exp.items() if eid in exps}}


@router.post("/api/experiences/{eid}/play", summary="Interactive story: open it, or pick a choice and get the next scene",
             tags=["Story"], body=PlayIn)
async def play(request: Request, body: PlayIn):
    state = st(request)
    e = _exp(request, request.path_params["eid"])
    if e["status"] != "ready":
        raise ApiError(409, "experience_not_ready", "This experience is not ready to play.")
    aud = next(a for a in state.repo.list_audiences(e["campaign_id"]) if a["id"] == e["audience_id"])
    scenes = e["scenes"]
    by_id = {s["id"]: s for s in scenes}
    ev = dict(campaign_id=e["campaign_id"], experience_id=e["id"], audience_name=aud["name"])
    if body.scene_id is None:
        scene = by_id[e["start_scene"]]
        state.repo.add_event(body.session_id, "experience_opened", scene_id=scene["id"], **ev)
    else:
        if not body.choice_id:
            raise bad_request("choice_id is required when scene_id is given.", "missing_choice")
        if body.scene_id not in by_id:
            raise bad_request("Unknown scene.", "unknown_scene")
        scene = next_scene(scenes, body.scene_id, body.choice_id)
        if scene is None:
            raise bad_request("That choice does not exist in this scene.", "unknown_choice")
        state.repo.add_event(body.session_id, "choice_selected", scene_id=body.scene_id, choice_id=body.choice_id, **ev)
    if scene.get("is_ending"):
        state.repo.add_event(body.session_id, "story_completed", scene_id=scene["id"], **ev)
    img = state.repo.current_image(e["id"], scene["id"], SCENE_ASPECT)
    url = f"/media/{img['file_name']}" if img else None
    hero = state.repo.current_image(e["id"], "hero", "4:3")
    return {"scene": public_scene(scene, url), "is_ending": bool(scene.get("is_ending")),
            "experience": {"id": e["id"], "headline": e["headline"], "cta": e["cta"], "audience": aud["name"],
                           "hero_url": f"/media/{hero['file_name']}" if hero else None,
                           "scene_count": len(scenes)}}


@router.post("/api/experiences/{eid}/images", summary="Generate hero and/or scene images (background job)",
             tags=["Images"], body=ImageGenIn, status=202, expensive=True)
async def generate_images(request: Request, body: ImageGenIn):
    state = st(request)
    e = _exp(request, request.path_params["eid"])
    ok, why = state.hub.image_ready()
    if not ok:
        raise ApiError(503, "image_provider_not_configured", why or "Image generation is not configured.")
    key = f"img:{e['id']}"
    active = state.db.one("SELECT id FROM jobs WHERE target_key=? AND status IN ('queued','running')", (key,))
    if active:
        return {"job": state.repo.get_job(active["id"]), "already_running": True, "images": state.repo.list_images(e["id"])}
    if body.scene_ids:
        unknown = set(body.scene_ids) - {s["id"] for s in e["scenes"]}
        if unknown:
            raise bad_request("Unknown scene id.", "unknown_scene")
    ids = state.images.plan(e["id"], body.scope, body.aspect_ratio, body.scene_ids, body.force)
    if not ids:
        return {"job": None, "already_running": False, "message": "These images already exist.",
                "images": state.repo.list_images(e["id"])}
    job, started = state.jobs.submit("generate_images", key, lambda progress: state.images.process_many(ids, progress),
                                     campaign_id=e["campaign_id"], experience_id=e["id"])
    return {"job": job, "already_running": not started, "images": state.repo.list_images(e["id"])}


@router.post("/api/images/{iid}/retry", summary="Retry a failed image", tags=["Images"], status=202, expensive=True)
async def retry_image(request: Request):
    state = st(request)
    row = state.repo.get_image(request.path_params["iid"])
    if not row:
        raise not_found("Image")
    if row["status"] != "failed":
        raise ApiError(409, "not_failed", "Only failed images can be retried.")
    ok, why = state.hub.image_ready()
    if not ok:
        raise ApiError(503, "image_provider_not_configured", why or "Image generation is not configured.")
    e = _exp(request, row["experience_id"])
    key = f"img:{e['id']}"
    active = state.db.one("SELECT id FROM jobs WHERE target_key=? AND status IN ('queued','running')", (key,))
    if active:
        return {"job": state.repo.get_job(active["id"]), "already_running": True}
    if state.repo.active_image(row["experience_id"], row["scene_key"], row["aspect_ratio"]):
        raise ApiError(409, "already_running", "This image is already being generated.")
    state.repo.update_image(row["id"], status="queued", error_code=None, error_message=None)
    job, started = state.jobs.submit("generate_images", key, lambda progress: state.images.process_many([row["id"]], progress),
                                     campaign_id=e["campaign_id"], experience_id=e["id"])
    return {"job": job, "already_running": not started}


@router.post("/api/events", summary="Record a viewer event (CTA click)", tags=["Analytics"], body=EventIn, status=201)
async def record_event(request: Request, body: EventIn):
    state = st(request)
    e = _exp(request, body.experience_id)
    aud = next(a for a in state.repo.list_audiences(e["campaign_id"]) if a["id"] == e["audience_id"])
    state.repo.add_event(body.session_id, body.type, campaign_id=e["campaign_id"], experience_id=e["id"],
                         audience_name=aud["name"])
    return {"recorded": True}
