"""Multilingual translation of a generated experience.

Gemma (Google open model) is the main translator. If Gemma fails, the Evolus agent translates instead (the chain is
defined in ``providers/hub.py``). The translation keeps the story structure intact: same scene ids, same choices.
Deterministic checks (numbers kept, prohibited phrases absent) are stored with every translation; they never invent text.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re

from pydantic import Field, model_validator

from ..config import Settings
from ..db import dumps
from ..providers.hub import GenerationFailed, ProviderHub
from ..repo import Repo
from ..schemas import _Out
from .prompts import translate_prompt

logger = logging.getLogger("vyra.translate")
_NUM = re.compile(r"\d+(?:[.,]\d+)*")


def source_payload(exp: dict) -> dict:
    return {
        "headline": exp["headline"], "body_copy": exp["body_copy"], "cta": exp["cta"],
        "scenes": [{"id": s["id"], "title": s["title"], "narration": s["narration"],
                    "choices": [{"id": c["id"], "label": c["label"]} for c in (s.get("choices") or [])]}
                   for s in exp["scenes"]],
    }


def source_hash(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:32]


def _model_for(payload: dict):
    ids = [s["id"] for s in payload["scenes"]]
    choice_ids = {s["id"]: [c["id"] for c in s["choices"]] for s in payload["scenes"]}

    class _Choice(_Out):
        id: str
        label: str = Field(..., min_length=1, max_length=240)

    class _Scene(_Out):
        id: str
        title: str = Field(..., min_length=1, max_length=240)
        narration: str = Field(..., min_length=1, max_length=2000)
        choices: list[_Choice] = Field(default_factory=list)

    class _Translation(_Out):
        headline: str = Field(..., min_length=1, max_length=400)
        body_copy: str = Field(..., min_length=1, max_length=3000)
        cta: str = Field(..., min_length=1, max_length=200)
        scenes: list[_Scene]

        @model_validator(mode="after")
        def _same_structure(self) -> "_Translation":
            if [s.id for s in self.scenes] != ids:
                raise ValueError("scenes must keep the same ids in the same order as the input")
            for s in self.scenes:
                if [c.id for c in s.choices] != choice_ids[s.id]:
                    raise ValueError(f"scene {s.id}: choices must keep the same ids as the input")
            return self

    return _Translation


def _numbers(text: str) -> list[str]:
    return sorted(_NUM.findall(text or ""))


def run_checks(campaign: dict, payload: dict, out: dict) -> list[dict]:
    checks: list[dict] = []
    for field in ("headline", "body_copy", "cta"):
        if _numbers(payload[field]) != _numbers(out[field]):
            checks.append({"level": "warning", "field": field,
                           "message": f"The numbers in the {field.replace('_', ' ')} differ from the original. "
                                      "Check prices, percentages and dates."})
    joined = " ".join([out["headline"], out["body_copy"], out["cta"]] +
                      [f"{s['title']} {s['narration']}" for s in out["scenes"]]).lower()
    for phrase in campaign.get("avoid") or []:
        if phrase and phrase.lower() in joined:
            checks.append({"level": "warning", "field": "text",
                           "message": f'The translation contains the prohibited phrase "{phrase}".'})
    return checks


class Translator:
    def __init__(self, settings: Settings, repo: Repo, hub: ProviderHub):
        self.s = settings
        self.repo = repo
        self.hub = hub

    async def translate(self, campaign: dict, exp: dict, language: str) -> dict:
        payload = source_payload(exp)
        system, user = translate_prompt(campaign, campaign["language"], language, payload)
        try:
            out = await self.hub.run_json(
                purpose="translate", system=system, user=user, model_cls=_model_for(payload),
                campaign_id=campaign["id"], experience_id=exp["id"], max_tokens=6000, temperature=0.2)
        except GenerationFailed:
            raise
        data = out.parsed.model_dump()
        checks = run_checks(campaign, payload, data)
        return self.repo.save_translation(exp["id"], language, data, source_hash(payload), checks, {
            "provider": out.provider, "model": out.model, "fallback_used": out.fallback_used,
            "attempts": out.attempts, "input_tokens": out.input_tokens, "output_tokens": out.output_tokens,
            "latency_ms": out.latency_ms, "cost_usd": out.cost_usd})


def with_stale(exp: dict, translations: list[dict]) -> list[dict]:
    """Mark translations whose source text changed (human edit or regeneration) after they were made."""
    current = source_hash(source_payload(exp))
    return [{**t, "stale": t["source_hash"] != current} for t in translations]
