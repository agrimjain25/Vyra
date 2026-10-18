"""Pydantic models: API payloads and the structures we require from the language model."""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .services.story import SCENE_ID, check_story_graph

Tone = Literal["Confident", "Warm", "Playful", "Premium"]
Objective = Literal["Brand awareness", "Product launch", "Engagement", "Conversions", "Retention"]
ASPECT_RATIOS = ("1:1", "4:3", "3:4", "3:2", "2:3", "16:9", "9:16", "4:5", "5:4", "21:9")
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _clean(v: str) -> str:
    return _CTRL.sub("", v).strip()


def _clean_items(v: list[str]) -> list[str]:
    out: list[str] = []
    for item in v:
        item = _clean(item)
        if item and len(item) > 300:
            raise ValueError("each item must be 300 characters or fewer")
        if item and item.lower() not in {x.lower() for x in out}:
            out.append(item)
    return out


class _In(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="ignore")


# --------------------------------------------------------------------------- brand / campaign

class BrandIn(_In):
    name: str = Field("", max_length=120)
    tone: Tone = "Confident"
    values: str = Field("", max_length=500)
    facts: list[str] = Field(default_factory=list, max_length=30)
    avoid: list[str] = Field(default_factory=list, max_length=30)

    @field_validator("facts", "avoid")
    @classmethod
    def _items(cls, v: list[str]) -> list[str]:
        return _clean_items(v)


class AudienceIn(_In):
    id: str | None = Field(None, max_length=64)
    name: str = Field(..., min_length=1, max_length=80)
    interests: str = Field("", max_length=200)
    needs: str = Field(..., min_length=1, max_length=240, description="Motivations / needs, written by you")
    angle: str = Field("Performance", min_length=1, max_length=60)
    context: str = Field("", max_length=300, description="Optional preferences or context. Do not include personal data.")
    source: Literal["user", "template", "ai_suggested"] = "user"

    @field_validator("name", "interests", "needs", "angle", "context")
    @classmethod
    def _c(cls, v: str) -> str:
        return _clean(v)


class CampaignIn(_In):
    name: str = Field("", max_length=160)
    brand: str = Field(..., min_length=1, max_length=120)
    product: str = Field(..., min_length=1, max_length=160)
    description: str = Field(..., min_length=1, max_length=800)
    objective: Objective = "Brand awareness"
    tone: Tone = "Confident"
    values: str = Field("", max_length=500)
    language: str = Field("English", min_length=2, max_length=40)
    approved_discount: str | None = Field(None, max_length=120)
    facts: list[str] = Field(default_factory=list, max_length=30)
    avoid: list[str] = Field(default_factory=list, max_length=30)
    audiences: list[AudienceIn] = Field(..., min_length=1, max_length=5)

    @field_validator("facts", "avoid")
    @classmethod
    def _items(cls, v: list[str]) -> list[str]:
        return _clean_items(v)

    @field_validator("name", "brand", "product", "description", "values", "language")
    @classmethod
    def _c(cls, v: str) -> str:
        return _clean(v)

    @field_validator("approved_discount")
    @classmethod
    def _disc(cls, v: str | None) -> str | None:
        v = _clean(v) if v else None
        return v or None

    @model_validator(mode="after")
    def _unique_audiences(self) -> "CampaignIn":
        names = [a.name.lower() for a in self.audiences]
        if len(set(names)) != len(names):
            raise ValueError("audiences: each audience needs a different name")
        return self


class GenerateIn(_In):
    audience_ids: list[str] | None = Field(None, max_length=5)
    review: bool = True


class ExperiencePatch(_In):
    headline: str | None = Field(None, min_length=1, max_length=150)
    body_copy: str | None = Field(None, min_length=1, max_length=1500)
    cta: str | None = Field(None, min_length=1, max_length=80)

    @model_validator(mode="after")
    def _any(self) -> "ExperiencePatch":
        if self.headline is None and self.body_copy is None and self.cta is None:
            raise ValueError("send at least one of headline, body_copy, cta")
        return self


class TranslateIn(_In):
    languages: list[str] = Field(..., min_length=1, max_length=4)

    @field_validator("languages")
    @classmethod
    def _langs(cls, v: list[str]) -> list[str]:
        out: list[str] = []
        for item in v:
            item = _clean(item)
            if not 2 <= len(item) <= 40:
                raise ValueError("each language must be 2 to 40 characters, for example 'Italian'")
            if item.lower() not in {x.lower() for x in out}:
                out.append(item)
        return out


class ImageGenIn(_In):
    scope: Literal["hero", "scenes", "all"] = "hero"
    aspect_ratio: str | None = None
    scene_ids: list[str] | None = Field(None, max_length=8)
    force: bool = False

    @field_validator("aspect_ratio")
    @classmethod
    def _ar(cls, v: str | None) -> str | None:
        if v is not None and v not in ASPECT_RATIOS:
            raise ValueError(f"aspect_ratio must be one of {', '.join(ASPECT_RATIOS)}")
        return v


class PlayIn(_In):
    session_id: str = Field(..., pattern=r"^[A-Za-z0-9_\-]{8,64}$")
    scene_id: str | None = Field(None, max_length=32)
    choice_id: str | None = Field(None, max_length=48)


class EventIn(_In):
    session_id: str = Field(..., pattern=r"^[A-Za-z0-9_\-]{8,64}$")
    type: Literal["cta_clicked", "experience_opened"]
    experience_id: str = Field(..., max_length=64)


class ReviewIn(_In):
    ai: bool = True


# --------------------------------------------------------------------------- LLM output contracts

class _Out(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


class StrategyAudience(_Out):
    audience_name: str
    distinct_angle: str = Field(..., min_length=5)
    key_message: str = Field(..., min_length=5)
    differentiation_notes: str = ""


class VisualIdentity(_Out):
    palette: str = Field(..., min_length=3)
    lighting: str = Field(..., min_length=3)
    composition: str = Field(..., min_length=3)
    motifs: str = ""
    avoid_visuals: str = ""


class StrategyOut(_Out):
    core_idea: str = Field(..., min_length=10)
    brand_essence: str = Field(..., min_length=5)
    voice_guidelines: str = Field(..., min_length=5)
    visual_identity: VisualIdentity
    audiences: list[StrategyAudience] = Field(..., min_length=1)


class ChoiceOut(_Out):
    id: str = Field(..., pattern=SCENE_ID.pattern)
    label: str = Field(..., min_length=2, max_length=90)
    next: str


class SceneOut(_Out):
    id: str = Field(..., pattern=SCENE_ID.pattern)
    title: str = Field(..., min_length=2, max_length=90)
    narration: str = Field(..., min_length=15, max_length=900)
    image_prompt: str = Field(..., min_length=15, max_length=900)
    choices: list[ChoiceOut] = Field(default_factory=list)
    is_ending: bool = False


class ExperienceOut(_Out):
    headline: str = Field(..., min_length=3, max_length=150)
    body_copy: str = Field(..., min_length=20, max_length=1500)
    cta: str = Field(..., min_length=2, max_length=80)
    creative_concept: str = Field(..., min_length=15, max_length=900)
    personalization_rationale: str = Field(..., min_length=15, max_length=900)
    visual_direction: str = Field(..., min_length=15, max_length=900)
    hero_image_prompt: str = Field(..., min_length=20, max_length=1200)
    start_scene: str
    scenes: list[SceneOut]

    @model_validator(mode="after")
    def _graph(self) -> "ExperienceOut":
        problems = check_story_graph([s.model_dump() for s in self.scenes], self.start_scene)
        if problems:
            raise ValueError("invalid story graph: " + "; ".join(problems))
        return self


class AIFinding(_Out):
    audience: str | None = None
    field: str | None = None
    category: Literal["unsupported_claim", "voice", "misleading_offer", "sensitive_inference",
                      "differentiation", "other"] = "other"
    severity: Literal["warning", "error"] = "warning"
    message: str = Field(..., min_length=3, max_length=400)
    evidence: str = Field("", max_length=300)
    suggestion: str = Field("", max_length=300)


class AIReviewOut(_Out):
    findings: list[AIFinding] = Field(default_factory=list, max_length=40)
    summary: str = ""
