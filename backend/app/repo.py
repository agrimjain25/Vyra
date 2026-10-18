"""Data access. Every function here is a short, synchronous SQLite call."""
from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from .db import Database, dumps, loads


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def new_id() -> str:
    return uuid.uuid4().hex


def media_url(file_name: str | None) -> str | None:
    return f"/media/{file_name}" if file_name else None


class Repo:
    def __init__(self, db: Database):
        self.db = db

    # ------------------------------------------------------------------ brand guide
    def get_brand(self) -> dict:
        row = self.db.one("SELECT * FROM brand_guide WHERE id = 1")
        if not row:
            return {"name": "", "tone": "Confident", "values": "", "facts": [], "avoid": [], "updated_at": None}
        return {"name": row["name"], "tone": row["tone"], "values": row["brand_values"],
                "facts": loads(row["facts"], []), "avoid": loads(row["avoid"], []), "updated_at": row["updated_at"]}

    def put_brand(self, data: dict) -> dict:
        with self.db.tx() as c:
            c.execute(
                """INSERT INTO brand_guide(id, name, tone, brand_values, facts, avoid, updated_at)
                   VALUES (1, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET name=excluded.name, tone=excluded.tone,
                     brand_values=excluded.brand_values, facts=excluded.facts, avoid=excluded.avoid,
                     updated_at=excluded.updated_at""",
                (data["name"], data["tone"], data["values"], dumps(data["facts"]), dumps(data["avoid"]), now()))
        return self.get_brand()

    def clear_brand(self) -> None:
        self.db.execute("DELETE FROM brand_guide WHERE id = 1")

    # ------------------------------------------------------------------ campaigns
    @staticmethod
    def _campaign_row(r: dict) -> dict:
        return {
            "id": r["id"], "name": r["name"], "brand": r["brand"], "product": r["product"],
            "description": r["description"], "objective": r["objective"], "tone": r["tone"],
            "values": r["brand_values"], "language": r["language"], "approved_discount": r["approved_discount"],
            "facts": loads(r["facts"], []), "avoid": loads(r["avoid"], []),
            "status": r["status"], "status_reason": r["status_reason"],
            "strategy": loads(r["strategy"]), "review": loads(r["review"]),
            "product_image_url": media_url(r["product_image"]), "product_image": r["product_image"],
            "created_at": r["created_at"], "updated_at": r["updated_at"],
        }

    def create_campaign(self, data: dict) -> str:
        cid = new_id()
        ts = now()
        name = data["name"] or f"{data['product']} {data['objective']}"
        with self.db.tx() as c:
            c.execute(
                """INSERT INTO campaigns(id, name, brand, product, description, objective, tone, brand_values,
                   language, approved_discount, facts, avoid, status, status_reason, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (cid, name, data["brand"], data["product"], data["description"], data["objective"], data["tone"],
                 data["values"], data["language"], data["approved_discount"], dumps(data["facts"]),
                 dumps(data["avoid"]), "draft", "No content generated yet.", ts, ts))
            for pos, a in enumerate(data["audiences"]):
                self._insert_audience(c, cid, pos, a)
        return cid

    @staticmethod
    def _insert_audience(c: sqlite3.Connection, cid: str, pos: int, a: dict) -> str:
        aid = new_id()
        c.execute(
            """INSERT INTO audiences(id, campaign_id, position, name, interests, needs, angle, context, source)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (aid, cid, pos, a["name"], a["interests"], a["needs"], a["angle"], a["context"], a["source"]))
        return aid

    def update_campaign(self, cid: str, data: dict) -> bool:
        """Update the brief. Returns True when generation-relevant content changed (experiences become stale)."""
        current = self.get_campaign_core(cid)
        if not current:
            raise KeyError(cid)
        old_auds = self.list_audiences(cid)
        by_id = {a["id"]: a for a in old_auds}
        changed = False
        relevant = ("brand", "product", "description", "objective", "tone", "values", "language",
                    "approved_discount", "facts", "avoid")
        for k in relevant:
            if (current[k] or None) != (data[k] or None) and (current[k] or "") != (data[k] or ""):
                changed = True
        name = data["name"] or f"{data['product']} {data['objective']}"
        ts = now()
        with self.db.tx() as c:
            c.execute(
                """UPDATE campaigns SET name=?, brand=?, product=?, description=?, objective=?, tone=?,
                   brand_values=?, language=?, approved_discount=?, facts=?, avoid=?, updated_at=? WHERE id=?""",
                (name, data["brand"], data["product"], data["description"], data["objective"], data["tone"],
                 data["values"], data["language"], data["approved_discount"], dumps(data["facts"]),
                 dumps(data["avoid"]), ts, cid))
            keep: set[str] = set()
            for pos, a in enumerate(data["audiences"]):
                aid = a.get("id")
                if aid and aid in by_id:
                    old = by_id[aid]
                    if any((old[k] or "") != (a[k] or "") for k in ("name", "interests", "needs", "angle", "context")):
                        changed = True
                        c.execute("UPDATE experiences SET status='stale', updated_at=? WHERE audience_id=?", (ts, aid))
                    c.execute(
                        """UPDATE audiences SET position=?, name=?, interests=?, needs=?, angle=?, context=?, source=?
                           WHERE id=?""",
                        (pos, a["name"], a["interests"], a["needs"], a["angle"], a["context"], a["source"], aid))
                    keep.add(aid)
                else:
                    keep.add(self._insert_audience(c, cid, pos, a))
                    changed = True
            for aid in set(by_id) - keep:
                changed = True
                c.execute("DELETE FROM audiences WHERE id=?", (aid,))
            if changed:
                c.execute("UPDATE experiences SET status='stale', updated_at=? WHERE campaign_id=? AND status='ready'",
                          (ts, cid))
                c.execute("UPDATE campaigns SET status='draft', status_reason=?, review=NULL WHERE id=?",
                          ("The brief was edited. Regenerate to refresh the content.", cid))
        return changed

    def get_campaign_core(self, cid: str) -> dict | None:
        r = self.db.one("SELECT * FROM campaigns WHERE id = ?", (cid,))
        return self._campaign_row(r) if r else None

    def list_audiences(self, cid: str) -> list[dict]:
        return self.db.query("SELECT * FROM audiences WHERE campaign_id = ? ORDER BY position", (cid,))

    def list_campaigns(self) -> list[dict]:
        rows = self.db.query(
            """SELECT c.*, (SELECT COUNT(*) FROM audiences a WHERE a.campaign_id = c.id) AS audience_count,
                      (SELECT COUNT(*) FROM experiences e WHERE e.campaign_id = c.id AND e.status = 'ready') AS ready_count
               FROM campaigns c ORDER BY c.updated_at DESC""")
        out = []
        for r in rows:
            d = self._campaign_row(r)
            d["audience_count"] = r["audience_count"]
            d["experience_count"] = r["ready_count"]
            for k in ("strategy", "review", "description", "product_image"):
                d.pop(k, None)
            d["finding_count"] = None
            out.append(d)
        for d in out:
            rev = loads(self.db.one("SELECT review FROM campaigns WHERE id=?", (d["id"],))["review"])
            d["finding_count"] = len([f for f in (rev or {}).get("findings", []) if f["severity"] != "info"]) if rev else None
            thumb = self.db.one(
                """SELECT i.file_name FROM images i JOIN experiences e ON e.id = i.experience_id
                   WHERE e.campaign_id = ? AND i.status='succeeded' AND i.scene_key='hero'
                   ORDER BY i.updated_at DESC LIMIT 1""", (d["id"],))
            d["thumb_url"] = media_url(thumb["file_name"]) if thumb else None
        return out

    def delete_campaign(self, cid: str) -> list[str]:
        """Delete and return media file names that should be removed from disk."""
        files = [r["file_name"] for r in self.db.query(
            """SELECT i.file_name FROM images i JOIN experiences e ON e.id = i.experience_id
               WHERE e.campaign_id = ? AND i.file_name IS NOT NULL""", (cid,))]
        core = self.db.one("SELECT product_image FROM campaigns WHERE id = ?", (cid,))
        if core and core["product_image"]:
            files.append(core["product_image"])
        with self.db.tx() as c:
            c.execute("DELETE FROM jobs WHERE campaign_id = ?", (cid,))
            n = c.execute("DELETE FROM campaigns WHERE id = ?", (cid,)).rowcount
        return files if n else []

    def duplicate_campaign(self, cid: str) -> str | None:
        core = self.get_campaign_core(cid)
        if not core:
            return None
        auds = self.list_audiences(cid)
        data = {
            "name": f"{core['name']} (copy)", "brand": core["brand"], "product": core["product"],
            "description": core["description"], "objective": core["objective"], "tone": core["tone"],
            "values": core["values"], "language": core["language"], "approved_discount": core["approved_discount"],
            "facts": core["facts"], "avoid": core["avoid"],
            "audiences": [{k: a[k] for k in ("name", "interests", "needs", "angle", "context", "source")} for a in auds],
        }
        return self.create_campaign(data)

    def set_campaign_status(self, cid: str, status: str, reason: str) -> None:
        self.db.execute("UPDATE campaigns SET status=?, status_reason=?, updated_at=? WHERE id=?",
                        (status, reason, now(), cid))

    def set_campaign_strategy(self, cid: str, strategy: dict | None) -> None:
        self.db.execute("UPDATE campaigns SET strategy=?, updated_at=? WHERE id=?",
                        (dumps(strategy) if strategy else None, now(), cid))

    def set_campaign_review(self, cid: str, review: dict | None) -> None:
        self.db.execute("UPDATE campaigns SET review=?, updated_at=? WHERE id=?",
                        (dumps(review) if review else None, now(), cid))

    def set_product_image(self, cid: str, file_name: str | None) -> str | None:
        old = self.db.one("SELECT product_image FROM campaigns WHERE id=?", (cid,))
        self.db.execute("UPDATE campaigns SET product_image=?, updated_at=? WHERE id=?", (file_name, now(), cid))
        return old["product_image"] if old else None

    # ------------------------------------------------------------------ experiences
    @staticmethod
    def _experience_row(r: dict) -> dict:
        return {
            "id": r["id"], "campaign_id": r["campaign_id"], "audience_id": r["audience_id"], "status": r["status"],
            "headline": r["headline"], "body_copy": r["body_copy"], "cta": r["cta"], "concept": r["concept"],
            "rationale": r["rationale"], "visual_direction": r["visual_direction"],
            "hero_image_prompt": r["hero_image_prompt"], "scenes": loads(r["scenes"], []),
            "start_scene": r["start_scene"],
            "generation": {"provider": r["provider"], "model": r["model"], "fallback_used": bool(r["fallback_used"]),
                           "attempts": r["attempts"], "input_tokens": r["input_tokens"],
                           "output_tokens": r["output_tokens"], "latency_ms": r["latency_ms"],
                           "cost_usd": r["cost_usd"]},
            "error": loads(r["error"]), "human_edited": bool(r["human_edited"]), "version": r["version"],
            "created_at": r["created_at"], "updated_at": r["updated_at"],
        }

    def get_experience(self, eid: str) -> dict | None:
        r = self.db.one("SELECT * FROM experiences WHERE id = ?", (eid,))
        return self._experience_row(r) if r else None

    def get_experience_for_audience(self, aid: str) -> dict | None:
        r = self.db.one("SELECT * FROM experiences WHERE audience_id = ?", (aid,))
        return self._experience_row(r) if r else None

    def list_experiences(self, cid: str) -> list[dict]:
        rows = self.db.query(
            """SELECT e.* FROM experiences e JOIN audiences a ON a.id = e.audience_id
               WHERE e.campaign_id = ? ORDER BY a.position""", (cid,))
        return [self._experience_row(r) for r in rows]

    def ensure_experience_row(self, cid: str, aid: str) -> str:
        r = self.db.one("SELECT id FROM experiences WHERE audience_id = ?", (aid,))
        if r:
            return r["id"]
        eid = new_id()
        ts = now()
        self.db.execute("INSERT INTO experiences(id, campaign_id, audience_id, status, created_at, updated_at) "
                        "VALUES (?,?,?,?,?,?)", (eid, cid, aid, "queued", ts, ts))
        return eid

    def set_experience_status(self, eid: str, status: str, error: dict | None = None) -> None:
        self.db.execute("UPDATE experiences SET status=?, error=?, updated_at=? WHERE id=?",
                        (status, dumps(error) if error else None, now(), eid))

    def save_experience_content(self, eid: str, out: dict, meta: dict) -> None:
        with self.db.tx() as c:
            c.execute(
                """UPDATE experiences SET status='ready', headline=?, body_copy=?, cta=?, concept=?, rationale=?,
                   visual_direction=?, hero_image_prompt=?, scenes=?, start_scene=?, provider=?, model=?,
                   fallback_used=?, attempts=?, input_tokens=?, output_tokens=?, latency_ms=?, cost_usd=?,
                   error=NULL, human_edited=0, version=version+1, updated_at=? WHERE id=?""",
                (out["headline"], out["body_copy"], out["cta"], out["creative_concept"],
                 out["personalization_rationale"], out["visual_direction"], out["hero_image_prompt"],
                 dumps(out["scenes"]), out["start_scene"], meta["provider"], meta["model"],
                 1 if meta["fallback_used"] else 0, meta["attempts"], meta["input_tokens"], meta["output_tokens"],
                 meta["latency_ms"], meta["cost_usd"], now(), eid))

    def patch_experience_text(self, eid: str, fields: dict) -> None:
        sets = ", ".join(f"{k} = ?" for k in fields)
        self.db.execute(f"UPDATE experiences SET {sets}, human_edited=1, updated_at=? WHERE id=?",
                        (*fields.values(), now(), eid))

    # ------------------------------------------------------------------ translations
    @staticmethod
    def _translation_row(r: dict) -> dict:
        return {
            "id": r["id"], "experience_id": r["experience_id"], "language": r["language"],
            "headline": r["headline"], "body_copy": r["body_copy"], "cta": r["cta"],
            "scenes": loads(r["scenes"], []), "source_hash": r["source_hash"], "checks": loads(r["checks"], []),
            "generation": {"provider": r["provider"], "model": r["model"], "fallback_used": bool(r["fallback_used"]),
                           "attempts": r["attempts"], "input_tokens": r["input_tokens"],
                           "output_tokens": r["output_tokens"], "latency_ms": r["latency_ms"],
                           "cost_usd": r["cost_usd"]},
            "created_at": r["created_at"], "updated_at": r["updated_at"],
        }

    def save_translation(self, eid: str, language: str, out: dict, source_hash: str, checks: list[dict],
                         meta: dict) -> dict:
        ts = now()
        with self.db.tx() as c:
            c.execute(
                """INSERT INTO translations(id, experience_id, language, headline, body_copy, cta, scenes, source_hash,
                       checks, provider, model, fallback_used, attempts, input_tokens, output_tokens, latency_ms,
                       cost_usd, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(experience_id, language) DO UPDATE SET headline=excluded.headline,
                       body_copy=excluded.body_copy, cta=excluded.cta, scenes=excluded.scenes,
                       source_hash=excluded.source_hash, checks=excluded.checks, provider=excluded.provider,
                       model=excluded.model, fallback_used=excluded.fallback_used, attempts=excluded.attempts,
                       input_tokens=excluded.input_tokens, output_tokens=excluded.output_tokens,
                       latency_ms=excluded.latency_ms, cost_usd=excluded.cost_usd, updated_at=excluded.updated_at""",
                (new_id(), eid, language, out["headline"], out["body_copy"], out["cta"], dumps(out["scenes"]),
                 source_hash, dumps(checks), meta["provider"], meta["model"], 1 if meta["fallback_used"] else 0,
                 meta["attempts"], meta["input_tokens"], meta["output_tokens"], meta["latency_ms"], meta["cost_usd"],
                 ts, ts))
        return self.get_translation(eid, language)  # type: ignore[return-value]

    def get_translation(self, eid: str, language: str) -> dict | None:
        r = self.db.one("SELECT * FROM translations WHERE experience_id = ? AND language = ?", (eid, language))
        return self._translation_row(r) if r else None

    def list_translations(self, eid: str) -> list[dict]:
        return [self._translation_row(r) for r in
                self.db.query("SELECT * FROM translations WHERE experience_id = ? ORDER BY created_at", (eid,))]

    def list_translations_for_campaign(self, cid: str) -> dict[str, list[dict]]:
        rows = self.db.query(
            """SELECT t.* FROM translations t JOIN experiences e ON e.id = t.experience_id
               WHERE e.campaign_id = ? ORDER BY t.created_at""", (cid,))
        out: dict[str, list[dict]] = {}
        for r in rows:
            out.setdefault(r["experience_id"], []).append(self._translation_row(r))
        return out

    # ------------------------------------------------------------------ images
    @staticmethod
    def _image_row(r: dict) -> dict:
        return {
            "id": r["id"], "experience_id": r["experience_id"], "scene_key": r["scene_key"],
            "aspect_ratio": r["aspect_ratio"], "status": r["status"], "error_code": r["error_code"],
            "error_message": r["error_message"], "url": media_url(r["file_name"]) if r["status"] == "succeeded" else None,
            "provider": r["provider"], "model": r["model"], "mime": r["mime"], "bytes": r["bytes"],
            "width": r["width"], "height": r["height"], "latency_ms": r["latency_ms"], "cost_usd": r["cost_usd"],
            "attempts": r["attempts"], "created_at": r["created_at"], "updated_at": r["updated_at"],
        }

    def list_images(self, eid: str) -> list[dict]:
        return [self._image_row(r) for r in self.db.query(
            "SELECT * FROM images WHERE experience_id = ? ORDER BY created_at", (eid,))]

    def list_images_for_campaign(self, cid: str) -> list[dict]:
        return [self._image_row(r) for r in self.db.query(
            """SELECT i.* FROM images i JOIN experiences e ON e.id = i.experience_id
               WHERE e.campaign_id = ? ORDER BY i.created_at""", (cid,))]

    def get_image(self, iid: str) -> dict | None:
        return self.db.one("SELECT * FROM images WHERE id = ?", (iid,))

    def get_image_public(self, iid: str) -> dict | None:
        r = self.get_image(iid)
        return self._image_row(r) if r else None

    def current_image(self, eid: str, scene_key: str, aspect: str) -> dict | None:
        return self.db.one(
            """SELECT * FROM images WHERE experience_id=? AND scene_key=? AND aspect_ratio=? AND status='succeeded'
               ORDER BY updated_at DESC LIMIT 1""", (eid, scene_key, aspect))

    def active_image(self, eid: str, scene_key: str, aspect: str) -> dict | None:
        return self.db.one(
            """SELECT * FROM images WHERE experience_id=? AND scene_key=? AND aspect_ratio=?
               AND status IN ('queued','running') LIMIT 1""", (eid, scene_key, aspect))

    def create_image_row(self, eid: str, scene_key: str, aspect: str, prompt: str, prompt_hash: str) -> str | None:
        """Returns the new id, or None if an active generation already exists (unique-index guard)."""
        iid = new_id()
        ts = now()
        try:
            self.db.execute(
                """INSERT INTO images(id, experience_id, scene_key, aspect_ratio, prompt, prompt_hash, status,
                   created_at, updated_at) VALUES (?,?,?,?,?,?, 'queued', ?, ?)""",
                (iid, eid, scene_key, aspect, prompt, prompt_hash, ts, ts))
            return iid
        except sqlite3.IntegrityError:
            return None

    def update_image(self, iid: str, **fields: Any) -> None:
        fields["updated_at"] = now()
        sets = ", ".join(f"{k} = ?" for k in fields)
        self.db.execute(f"UPDATE images SET {sets} WHERE id = ?", (*fields.values(), iid))

    def delete_images(self, where: str, params: tuple) -> list[str]:
        rows = self.db.query(f"SELECT id, file_name FROM images WHERE {where}", params)
        if rows:
            self.db.execute(f"DELETE FROM images WHERE {where}", params)
        return [r["file_name"] for r in rows if r["file_name"]]

    def count_image_attempts_since(self, hours: int = 24) -> int:
        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        r = self.db.one("SELECT COUNT(*) AS n FROM provider_calls WHERE purpose='image' AND ts >= ?", (since,))
        return r["n"] if r else 0

    # ------------------------------------------------------------------ jobs
    @staticmethod
    def _job_row(r: dict) -> dict:
        return {"id": r["id"], "kind": r["kind"], "target_key": r["target_key"], "campaign_id": r["campaign_id"],
                "experience_id": r["experience_id"], "status": r["status"], "progress": loads(r["progress"], {}),
                "result": loads(r["result"]), "error": loads(r["error"]),
                "created_at": r["created_at"], "updated_at": r["updated_at"]}

    def create_job(self, kind: str, target_key: str, campaign_id: str | None, experience_id: str | None) -> tuple[dict, bool]:
        """(job, created). If an identical job is already queued/running, returns that one with created=False."""
        jid = new_id()
        ts = now()
        try:
            self.db.execute(
                """INSERT INTO jobs(id, kind, target_key, campaign_id, experience_id, status, created_at, updated_at)
                   VALUES (?,?,?,?,?, 'queued', ?, ?)""", (jid, kind, target_key, campaign_id, experience_id, ts, ts))
            return self.get_job(jid), True  # type: ignore[return-value]
        except sqlite3.IntegrityError:
            existing = self.db.one("SELECT * FROM jobs WHERE target_key=? AND status IN ('queued','running')",
                                   (target_key,))
            return self._job_row(existing), False  # type: ignore[arg-type]

    def get_job(self, jid: str) -> dict | None:
        r = self.db.one("SELECT * FROM jobs WHERE id = ?", (jid,))
        return self._job_row(r) if r else None

    def update_job(self, jid: str, **fields: Any) -> None:
        for k in ("progress", "result", "error"):
            if k in fields and fields[k] is not None:
                fields[k] = dumps(fields[k])
        fields["updated_at"] = now()
        sets = ", ".join(f"{k} = ?" for k in fields)
        self.db.execute(f"UPDATE jobs SET {sets} WHERE id = ?", (*fields.values(), jid))

    def active_jobs(self, cid: str) -> list[dict]:
        return [self._job_row(r) for r in self.db.query(
            "SELECT * FROM jobs WHERE campaign_id=? AND status IN ('queued','running') ORDER BY created_at", (cid,))]

    def fail_stale_jobs(self) -> int:
        err = dumps({"code": "interrupted", "message": "The server restarted while this was running. Please retry."})
        n = self.db.execute("UPDATE jobs SET status='failed', error=?, updated_at=? WHERE status IN ('queued','running')",
                            (err, now()))
        self.db.execute("UPDATE images SET status='failed', error_code='interrupted', "
                        "error_message='The server restarted while this image was generating. Retry it.', updated_at=? "
                        "WHERE status IN ('queued','running')", (now(),))
        self.db.execute("UPDATE experiences SET status='failed', error=?, updated_at=? WHERE status IN ('queued','generating')",
                        (err, now()))
        return n

    # ------------------------------------------------------------------ events & provider calls
    def add_event(self, session_id: str, type_: str, *, campaign_id: str | None = None,
                  experience_id: str | None = None, audience_name: str | None = None,
                  scene_id: str | None = None, choice_id: str | None = None) -> None:
        self.db.execute(
            """INSERT INTO events(session_id, type, campaign_id, experience_id, audience_name, scene_id, choice_id, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (session_id, type_, campaign_id, experience_id, audience_name, scene_id, choice_id, now()))

    def add_call(self, **f: Any) -> None:
        cols = ("purpose", "provider", "model", "endpoint_host", "ok", "error_code", "error_message", "latency_ms",
                "input_tokens", "output_tokens", "cost_usd", "request_id", "campaign_id", "experience_id", "attempt",
                "fallback_used")
        vals = [f.get(c) for c in cols]
        vals[cols.index("ok")] = 1 if f.get("ok") else 0
        vals[cols.index("fallback_used")] = 1 if f.get("fallback_used") else 0
        msg = vals[cols.index("error_message")]
        if msg:
            vals[cols.index("error_message")] = str(msg)[:300]
        self.db.execute(
            f"INSERT INTO provider_calls(ts, {', '.join(cols)}) VALUES (?, {', '.join('?' * len(cols))})",
            (now(), *vals))
