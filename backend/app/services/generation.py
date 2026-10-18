"""Campaign generation: shared strategy, one experience per audience (in parallel), then review."""
from __future__ import annotations

import asyncio
import logging

from ..config import Settings
from ..logging_setup import log
from ..providers.hub import GenerationFailed, ProviderHub
from ..repo import Repo
from ..schemas import ExperienceOut, StrategyOut
from . import safety
from .evolus_blocks import EvolusBlocks
from .prompts import experience_prompt, strategy_prompt

logger = logging.getLogger("vyra.generation")


class Generator:
    def __init__(self, settings: Settings, repo: Repo, hub: ProviderHub, media):
        self.s = settings
        self.repo = repo
        self.hub = hub
        self.media = media
        self.evolus_blocks = EvolusBlocks(settings, repo)  # optional Evolus AI Tool; does nothing unless switched on

    # ------------------------------------------------------------------ strategy
    async def ensure_strategy(self, campaign: dict, audiences: list[dict], force: bool) -> dict:
        if campaign.get("strategy") and not force:
            names = {s["audience_name"].strip().lower() for s in campaign["strategy"]["audiences"]}
            if {a["name"].strip().lower() for a in audiences} <= names:
                return campaign["strategy"]
        system, user = strategy_prompt(campaign, audiences)
        out = await self.hub.run_json(purpose="strategy", system=system, user=user, model_cls=StrategyOut,
                                      campaign_id=campaign["id"], max_tokens=2500)
        strategy: StrategyOut = out.parsed  # type: ignore[assignment]
        data = strategy.model_dump()
        # Map strategy rows to the audiences by name; fall back to order if the model renamed them.
        by_name = {s["audience_name"].strip().lower(): s for s in data["audiences"]}
        fixed = []
        for i, a in enumerate(audiences):
            row = by_name.get(a["name"].strip().lower())
            if row is None and len(data["audiences"]) == len(audiences):
                row = data["audiences"][i]
            if row is None:
                raise GenerationFailed("invalid_output", f'The strategy did not cover the audience "{a["name"]}".', [])
            fixed.append({**row, "audience_name": a["name"]})
        data["audiences"] = fixed
        data["meta"] = {"provider": out.provider, "model": out.model, "fallback_used": out.fallback_used,
                        "input_tokens": out.input_tokens, "output_tokens": out.output_tokens,
                        "latency_ms": out.latency_ms, "cost_usd": out.cost_usd}
        self.repo.set_campaign_strategy(campaign["id"], data)
        return data

    # ------------------------------------------------------------------ one audience
    async def generate_experience(self, campaign: dict, audiences: list[dict], audience: dict, strategy: dict) -> dict:
        eid = self.repo.ensure_experience_row(campaign["id"], audience["id"])
        self.repo.set_experience_status(eid, "generating")
        others = [a for a in audiences if a["id"] != audience["id"]]
        system, user = experience_prompt(campaign, audience, others, strategy)
        try:
            out = await self.hub.run_json(purpose="experience", system=system, user=user, model_cls=ExperienceOut,
                                          campaign_id=campaign["id"], experience_id=eid)
        except GenerationFailed as exc:
            self.repo.set_experience_status(eid, "failed", exc.as_dict())
            raise
        exp: ExperienceOut = out.parsed  # type: ignore[assignment]
        # New content means the old images (and their files) no longer match the scenes.
        for f in self.repo.delete_images("experience_id = ?", (eid,)):
            self.media.delete(f)
        self.repo.save_experience_content(eid, exp.model_dump(), {
            "provider": out.provider, "model": out.model, "fallback_used": out.fallback_used,
            "attempts": out.attempts, "input_tokens": out.input_tokens, "output_tokens": out.output_tokens,
            "latency_ms": out.latency_ms, "cost_usd": out.cost_usd})
        return {"experience_id": eid, "audience": audience["name"], "ok": True}

    # ------------------------------------------------------------------ whole campaign
    async def generate_campaign(self, campaign_id: str, audience_ids: list[str] | None, run_review: bool,
                                progress) -> dict:
        campaign = self.repo.get_campaign_core(campaign_id)
        if not campaign:
            raise GenerationFailed("not_found", "The campaign no longer exists.", [])
        audiences = self.repo.list_audiences(campaign_id)
        targets = [a for a in audiences if not audience_ids or a["id"] in audience_ids]
        for a in targets:  # show "queued" immediately
            self.repo.set_experience_status(self.repo.ensure_experience_row(campaign_id, a["id"]), "queued")

        progress(step="strategy", message="Planning the shared creative platform…", done=0, total=len(targets))
        try:
            strategy = await self.ensure_strategy(campaign, audiences, force=audience_ids is None)
        except GenerationFailed as exc:
            for a in targets:
                self.repo.set_experience_status(self.repo.ensure_experience_row(campaign_id, a["id"]), "failed", exc.as_dict())
            self.refresh_status(campaign_id)
            raise

        results: list[dict] = []
        done = 0

        async def one(a: dict) -> None:
            nonlocal done
            try:
                results.append(await self.generate_experience(campaign, audiences, a, strategy))
            except GenerationFailed as exc:
                results.append({"audience": a["name"], "ok": False, "error": exc.as_dict()})
            except Exception as exc:  # noqa: BLE001
                logger.exception("experience generation crashed")
                eid = self.repo.ensure_experience_row(campaign_id, a["id"])
                err = {"code": "internal_error", "message": "Something went wrong on the server."}
                self.repo.set_experience_status(eid, "failed", err)
                results.append({"audience": a["name"], "ok": False, "error": err})
            done += 1
            progress(step="experiences", message=f"Generated {done} of {len(targets)} experiences…",
                     done=done, total=len(targets))

        progress(step="experiences", message="Writing audience-specific experiences…", done=0, total=len(targets))
        await asyncio.gather(*(one(a) for a in targets))

        failed = [r for r in results if not r["ok"]]
        if run_review:
            progress(step="review", message="Running brand-safety review…")
            await self.review_campaign(campaign_id, use_ai=True)
        else:
            self.refresh_status(campaign_id)
        if failed and len(failed) == len(targets):
            first = failed[0]["error"]
            raise GenerationFailed(first.get("code", "generation_failed"), first.get("message", "Generation failed."),
                                   first.get("attempts", []))
        log(logger, logging.INFO, "campaign generated", campaign=campaign_id, ok=len(results) - len(failed),
            failed=len(failed))
        return {"generated": len(results) - len(failed), "failed": len(failed), "results": results}

    # ------------------------------------------------------------------ review + status
    async def review_campaign(self, campaign_id: str, use_ai: bool) -> dict:
        campaign = self.repo.get_campaign_core(campaign_id)
        audiences = self.repo.list_audiences(campaign_id)
        experiences = self.repo.list_experiences(campaign_id)
        det = safety.deterministic_review(campaign, audiences, experiences)
        ai = await safety.ai_review(self.hub, campaign, audiences, experiences) if use_ai else None
        review = safety.build_review(det, ai)
        self.repo.set_campaign_review(campaign_id, review)
        self.refresh_status(campaign_id)
        if self.evolus_blocks.enabled:  # optional, informational, can never break the review
            try:
                await self.evolus_blocks.run_for_campaign(campaign_id)
            except Exception:  # noqa: BLE001
                logger.warning("Evolus tone check failed; the review is unaffected", exc_info=True)
        return review

    def rerun_deterministic(self, campaign_id: str) -> None:
        """Cheap, synchronous re-check after a human edits text. Keeps earlier AI findings out (they may be stale)."""
        campaign = self.repo.get_campaign_core(campaign_id)
        det = safety.deterministic_review(campaign, self.repo.list_audiences(campaign_id),
                                          self.repo.list_experiences(campaign_id))
        ai = {"status": "skipped", "message": "Text was edited after the AI review; run the review again.", "findings": []}
        self.repo.set_campaign_review(campaign_id, safety.build_review(det, ai))
        self.refresh_status(campaign_id)

    def refresh_status(self, campaign_id: str) -> None:
        campaign = self.repo.get_campaign_core(campaign_id)
        status, reason = safety.compute_status(self.repo.list_audiences(campaign_id),
                                               self.repo.list_experiences(campaign_id), campaign["review"], self.s)
        self.repo.set_campaign_status(campaign_id, status, reason)
