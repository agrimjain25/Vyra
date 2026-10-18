"""Interactive story graph: validation and traversal.

A story is a directed acyclic graph of scenes. Every non-ending scene offers 2-3 choices that lead
to *different* scenes, so a choice really changes what the viewer sees next.
"""
from __future__ import annotations

import re
from typing import Any

SCENE_ID = re.compile(r"^[A-Za-z0-9_\-]{1,32}$")
MIN_SCENES, MAX_SCENES = 3, 8


def check_story_graph(scenes: list[dict], start: str | None) -> list[str]:
    """Return human-readable problems (empty list = valid). Used to drive retry prompts."""
    errors: list[str] = []
    if not (MIN_SCENES <= len(scenes) <= MAX_SCENES):
        errors.append(f"story must have {MIN_SCENES}-{MAX_SCENES} scenes (got {len(scenes)})")
    ids = [s.get("id") for s in scenes]
    if len(set(ids)) != len(ids):
        errors.append("scene ids must be unique")
    for sid in ids:
        if not isinstance(sid, str) or not SCENE_ID.match(sid):
            errors.append(f"invalid scene id {sid!r}")
    by_id = {s["id"]: s for s in scenes if isinstance(s.get("id"), str)}
    if start not in by_id:
        errors.append(f"start_scene {start!r} is not one of the scene ids")
        return errors

    for s in scenes:
        sid = s.get("id")
        choices = s.get("choices") or []
        if s.get("is_ending"):
            if choices:
                errors.append(f"ending scene {sid} must not have choices")
            continue
        if not (2 <= len(choices) <= 3):
            errors.append(f"scene {sid} needs 2-3 choices (got {len(choices)})")
        targets = [c.get("next") for c in choices]
        labels = [str(c.get("label", "")).strip().lower() for c in choices]
        cids = [c.get("id") for c in choices]
        if len(set(targets)) != len(targets):
            errors.append(f"choices in scene {sid} must lead to different scenes")
        if len(set(labels)) != len(labels):
            errors.append(f"choices in scene {sid} must have different labels")
        if len(set(cids)) != len(cids):
            errors.append(f"choice ids in scene {sid} must be unique")
        for c in choices:
            if c.get("next") not in by_id:
                errors.append(f"choice {c.get('id')!r} in scene {sid} points to unknown scene {c.get('next')!r}")
            elif c.get("next") == sid:
                errors.append(f"choice {c.get('id')!r} in scene {sid} points back to its own scene")
    if errors:
        return errors

    # reachability + cycle detection (iterative DFS with colours)
    WHITE, GREY, BLACK = 0, 1, 2
    colour = {sid: WHITE for sid in by_id}
    reachable: set[str] = set()
    stack: list[tuple[str, int]] = [(start, 0)]
    colour[start] = GREY
    reachable.add(start)
    while stack:
        sid, idx = stack.pop()
        choices = by_id[sid].get("choices") or []
        if idx < len(choices):
            stack.append((sid, idx + 1))
            nxt = choices[idx]["next"]
            if colour[nxt] == GREY:
                errors.append(f"story contains a loop through scene {nxt}")
                return errors
            if colour[nxt] == WHITE:
                colour[nxt] = GREY
                reachable.add(nxt)
                stack.append((nxt, 0))
        else:
            colour[sid] = BLACK
    unreachable = sorted(set(by_id) - reachable)
    if unreachable:
        errors.append(f"scenes not reachable from the start: {', '.join(unreachable)}")
    endings = [sid for sid in reachable if by_id[sid].get("is_ending")]
    if len(endings) < 2:
        errors.append("story needs at least two different reachable endings")
    return errors


def public_scene(scene: dict, image_url: str | None = None) -> dict[str, Any]:
    """What a viewer is allowed to see: no ``next`` pointers, so the server decides where a choice leads."""
    return {
        "id": scene["id"],
        "title": scene.get("title", ""),
        "narration": scene.get("narration", ""),
        "is_ending": bool(scene.get("is_ending")),
        "choices": [{"id": c["id"], "label": c["label"]} for c in scene.get("choices", [])],
        "image_url": image_url,
    }


def next_scene(scenes: list[dict], current_id: str, choice_id: str) -> dict | None:
    """Resolve a choice on the server from the persisted graph."""
    by_id = {s["id"]: s for s in scenes}
    cur = by_id.get(current_id)
    if not cur:
        return None
    for c in cur.get("choices", []):
        if c["id"] == choice_id:
            return by_id.get(c["next"])
    return None


def story_stats(scenes: list[dict]) -> dict[str, int]:
    branches = sum(1 for s in scenes if len(s.get("choices", [])) > 1)
    return {"scenes": len(scenes), "branch_points": branches,
            "endings": sum(1 for s in scenes if s.get("is_ending"))}
