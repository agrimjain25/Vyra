"""Image generation workflow: queue rows, call the image provider, store the file, record the result."""
from __future__ import annotations

import hashlib
import logging

from ..config import Settings
from ..errors import ApiError
from ..logging_setup import log
from ..providers.base import ProviderError
from ..providers.hub import GenerationFailed, ProviderHub
from ..repo import Repo
from .media import MediaStore
from .prompts import build_image_prompt

logger = logging.getLogger("vyra.images")
DEFAULT_HERO_ASPECT = "4:3"
SCENE_ASPECT = "4:3"
MAX_IMAGE_BYTES = 15 * 1024 * 1024


class ImageService:
    def __init__(self, settings: Settings, repo: Repo, hub: ProviderHub, media: MediaStore):
        self.s = settings
        self.repo = repo
        self.hub = hub
        self.media = media

    def plan(self, experience_id: str, scope: str, aspect: str | None, scene_ids: list[str] | None, force: bool) -> list[str]:
        """Create queued rows for what needs generating. Returns image ids to process.

        Existing successful images are reused unless ``force`` (saves cost); an identical queued/running
        request is never duplicated (the database refuses it).
        """
        exp = self.repo.get_experience(experience_id)
        if not exp or exp["status"] not in ("ready", "stale"):
            raise ApiError(409, "experience_not_ready", "Generate the campaign content before generating images.")
        campaign = self.repo.get_campaign_core(exp["campaign_id"])
        audience = next(a for a in self.repo.list_audiences(exp["campaign_id"]) if a["id"] == exp["audience_id"])
        has_ref = bool(campaign["product_image"] and self.media.path(campaign["product_image"])) \
            and self.hub.image.supports_reference
        targets: list[tuple[str, str, str]] = []  # (scene_key, aspect, raw prompt)
        if scope in ("hero", "all"):
            targets.append(("hero", aspect or DEFAULT_HERO_ASPECT, exp["hero_image_prompt"]))
        if scope in ("scenes", "all"):
            for s in exp["scenes"]:
                if scene_ids and s["id"] not in scene_ids:
                    continue
                targets.append((s["id"], aspect or SCENE_ASPECT, s["image_prompt"]))
        ids: list[str] = []
        for key, ar, raw in targets:
            if not force and self.repo.current_image(experience_id, key, ar):
                continue
            prompt = build_image_prompt(campaign, campaign["strategy"], raw, audience, has_ref)
            phash = hashlib.sha256(f"{prompt}|{ar}".encode()).hexdigest()
            iid = self.repo.create_image_row(experience_id, key, ar, prompt, phash)
            if iid:
                ids.append(iid)
        return ids

    async def process(self, image_id: str) -> dict:
        row = self.repo.get_image(image_id)
        if not row:
            return {"ok": False}
        exp = self.repo.get_experience(row["experience_id"])
        campaign = self.repo.get_campaign_core(exp["campaign_id"]) if exp else None
        if not exp or not campaign:
            self.repo.update_image(image_id, status="failed", error_code="not_found", error_message="Experience was deleted.")
            return {"ok": False}
        # Cost guard: a hard daily ceiling on provider image calls.
        in_flight = self.repo.db.one("SELECT COUNT(*) n FROM images WHERE status='running' AND id != ?", (image_id,))['n']
        if self.s.image_max_per_day and self.repo.count_image_attempts_since(24) + in_flight >= self.s.image_max_per_day:
            self.repo.update_image(image_id, status="failed", error_code="daily_cap",
                                   error_message=f"The daily image limit ({self.s.image_max_per_day}) was reached. "
                                                 "Raise IMAGE_MAX_PER_DAY in backend/.env if this is intended.")
            return {"ok": False}
        self.repo.update_image(image_id, status="running", attempts=row["attempts"] + 1)
        reference = None
        if campaign["product_image"]:
            reference = self.media.read(campaign["product_image"])
        # Main provider first (Cloudflare). The backup (Pollinations) is tried ONLY if the main one fails.
        chain = [self.hub.image]
        if self.hub.image_backup is not None:
            chain.append(self.hub.image_backup)
        last_exc: ProviderError | None = None
        for pidx, provider in enumerate(chain):
            if not provider.configured()[0]:
                if pidx == 0:
                    last_exc = ProviderError(provider.name, "missing_credentials", provider.configured()[1] or "not configured")
                continue
            if pidx > 0:
                log(logger, logging.WARNING, "main image provider failed; using backup", image=image_id,
                    main=chain[0].name, backup=provider.name, code=last_exc.code if last_exc else None)
            for attempt in (1, 2):  # one bounded retry for transient failures
                try:
                    async with self.hub.image_sem:
                        res = await provider.generate(row["prompt"], row["aspect_ratio"], reference, self.s.image_timeout_s)
                except ProviderError as exc:
                    last_exc = exc
                    self.repo.add_call(purpose="image", provider=provider.name, model=provider.model,
                                       endpoint_host=provider.endpoint_host, ok=False, error_code=exc.code,
                                       error_message=exc.message, campaign_id=exp["campaign_id"],
                                       experience_id=exp["id"], attempt=attempt, fallback_used=pidx > 0)
                    if exc.retryable and attempt == 1:
                        continue
                    break
                if len(res.data) > MAX_IMAGE_BYTES:
                    last_exc = ProviderError(provider.name, "bad_response", "The returned image is too large.")
                    break
                try:
                    saved = self.media.save(res.data, res.mime)
                except ValueError:
                    last_exc = ProviderError(provider.name, "bad_response",
                                             "The provider returned data that is not a valid PNG, JPEG or WebP image.")
                    self.repo.add_call(purpose="image", provider=provider.name, model=res.model,
                                       endpoint_host=res.endpoint_host, ok=False, error_code="bad_response",
                                       error_message="not an image", latency_ms=res.latency_ms,
                                       campaign_id=exp["campaign_id"], experience_id=exp["id"], attempt=attempt,
                                       fallback_used=pidx > 0)
                    break
                # Replace older successful images for the same slot so disk does not grow unbounded.
                for f in self.repo.delete_images(
                        "experience_id = ? AND scene_key = ? AND aspect_ratio = ? AND status = 'succeeded' AND id != ?",
                        (row["experience_id"], row["scene_key"], row["aspect_ratio"], image_id)):
                    self.media.delete(f)
                self.repo.update_image(image_id, status="succeeded", error_code=None, error_message=None,
                                       provider=res.provider, model=res.model, latency_ms=res.latency_ms,
                                       cost_usd=res.cost_usd, **saved)
                self.repo.add_call(purpose="image", provider=res.provider, model=res.model, endpoint_host=res.endpoint_host,
                                   ok=True, latency_ms=res.latency_ms, cost_usd=res.cost_usd, request_id=res.request_id,
                                   campaign_id=exp["campaign_id"], experience_id=exp["id"], attempt=attempt,
                                   fallback_used=pidx > 0)
                log(logger, logging.INFO, "image stored", image=image_id, bytes=saved["bytes"], ms=res.latency_ms,
                    provider=res.provider)
                return {"ok": True}
        assert last_exc is not None
        self.repo.update_image(image_id, status="failed", error_code=last_exc.code, error_message=last_exc.message)
        return {"ok": False, "error": last_exc.as_dict()}

    async def process_many(self, ids: list[str], progress) -> dict:
        import asyncio
        done = 0
        results = []

        async def one(i: str) -> None:
            nonlocal done
            results.append(await self.process(i))
            done += 1
            progress(step="images", message=f"Generated {done} of {len(ids)} images…", done=done, total=len(ids))

        progress(step="images", message="Generating images…", done=0, total=len(ids))
        await asyncio.gather(*(one(i) for i in ids))
        ok = sum(1 for r in results if r.get("ok"))
        if ids and ok == 0:
            first = next((r.get("error") for r in results if r.get("error")), None) or {
                "code": "image_failed", "message": "No image could be generated."}
            raise GenerationFailed(first.get("code", "image_failed"), first.get("message", "Image generation failed."), [])
        return {"images_ok": ok, "images_failed": len(ids) - ok}
