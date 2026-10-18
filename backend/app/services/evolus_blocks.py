"""Evolus building block: the AI Tool "Sentiment", driven from VYRA through the Evolus REST API.

Why this exists: the Evolus track asks the app to *drive* Evolus through its APIs and use at least two building
blocks. The chat endpoint (agent) is already used for the review. This module adds a second, separate API call:
``POST {EVOLUS_REST_BASE_URL}/ai-tools/{tool}/run`` with the headers ``X-ApiKey`` and ``X-Application-Id``.

Safety rules of this module (it must never hurt the rest of the app):
* OFF by default. Nothing runs unless ``EVOLUS_SENTIMENT=true`` and the Evolus key + application id are set.
* Informational only. A tone result never changes a finding, a campaign status or "Ready".
* Never raises into the caller. Any failure is stored as a failed row and the campaign flow goes on.
* Results live in their own table (``evolus_tone_checks``); the existing tables and cost figures are untouched.

What is NOT known to this code: the exact request body and response shape of the Evolus Sentiment tool. The body
field name is configurable (``EVOLUS_TOOL_INPUT_FIELD``, default ``text``), extra body fields can be added with
``EVOLUS_TOOL_EXTRA_JSON``, and the raw response is always stored so you can see what Evolus really returned.
Check the "interactive API reference" in your Evolus workspace and adjust these two settings if needed.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger("vyra.evolus_blocks")

TABLE_SQL = """
CREATE TABLE IF NOT EXISTS evolus_tone_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    campaign_id TEXT REFERENCES campaigns(id) ON DELETE CASCADE,
    experience_id TEXT,
    audience_name TEXT,
    field TEXT NOT NULL,
    text_preview TEXT,
    tool TEXT,
    endpoint_host TEXT,
    ok INTEGER NOT NULL,
    label TEXT,
    score REAL,
    latency_ms INTEGER,
    http_status INTEGER,
    error_code TEXT,
    error_message TEXT,
    raw TEXT
)
"""
INDEX_SQL = "CREATE INDEX IF NOT EXISTS ix_tone_campaign ON evolus_tone_checks(campaign_id)"

NOTE = ("Informational only. This is the Evolus 'Sentiment' AI Tool called through the Evolus REST API. "
        "It never changes the brand checks or whether a campaign is Ready.")

_LABEL_KEYS = ("sentiment", "label", "classification", "tone", "prediction", "category", "result", "output")
_SCORE_KEYS = ("score", "confidence", "probability", "compound", "polarity")
MAX_TEXT = 1500
CONCURRENCY = 4
CALL_TIMEOUT_S = 20.0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _host(url: str | None) -> str:
    return urlparse(url or "").netloc or ""


def _is_loopback(host: str) -> bool:
    h = host.split(":")[0].lower()
    return h in ("localhost", "127.0.0.1", "::1", "[::1]", "0.0.0.0") or h.startswith("127.")


def parse_tone(data: Any, _depth: int = 0) -> tuple[str | None, float | None]:
    """Find a (label, score) in an unknown JSON reply. Returns (None, None) when nothing recognisable is there.

    This is deliberately forgiving because the exact shape is not documented here. It never invents a value.
    """
    label: str | None = None
    score: float | None = None
    if isinstance(data, str):
        return (data.strip()[:60] or None), None
    if isinstance(data, list):
        return parse_tone(data[0], _depth + 1) if data and _depth < 4 else (None, None)
    if not isinstance(data, dict):
        return None, None
    for k in _SCORE_KEYS:
        v = data.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            score = float(v)
            break
    for k in _LABEL_KEYS:
        v = data.get(k)
        if isinstance(v, str) and v.strip():
            label = v.strip()[:60]
            break
        if isinstance(v, (dict, list)) and _depth < 4:
            l2, s2 = parse_tone(v, _depth + 1)
            if l2:
                label = l2
                if score is None:
                    score = s2
                break
        if isinstance(v, (int, float)) and not isinstance(v, bool) and score is None:
            score = float(v)
    if label is None and _depth < 4:
        for k in ("data", "response", "results", "outputs"):
            if k in data:
                l2, s2 = parse_tone(data[k], _depth + 1)
                if l2 or s2 is not None:
                    return l2, (score if score is not None else s2)
    return label, score


def _error_from_status(status: int, body_text: str) -> tuple[str, str]:
    snippet = (body_text or "").strip().replace("\n", " ")[:200]
    if status in (401, 403):
        return "auth_failed", f"Evolus rejected the API key or application id ({status}). Check EVOLUS_API_KEY and EVOLUS_APP_ID. {snippet}".strip()
    if status == 402:
        return "quota_exhausted", f"Evolus says credits are used up. {snippet}".strip()
    if status == 404:
        return "not_found", ("Evolus could not find that tool or URL (404). Check EVOLUS_SENTIMENT_TOOL and EVOLUS_REST_BASE_URL "
                             f"against the interactive API reference in your workspace. {snippet}").strip()
    if status in (400, 422):
        return "bad_request", ("Evolus rejected the request body. The input field name may differ: set EVOLUS_TOOL_INPUT_FIELD "
                               f"(and EVOLUS_TOOL_EXTRA_JSON if more fields are needed). {snippet}").strip()
    if status == 429:
        return "rate_limited", f"Evolus is rate limiting requests. {snippet}".strip()
    if status >= 500:
        return "provider_unavailable", f"Evolus is temporarily unavailable ({status}). {snippet}".strip()
    return "bad_request", f"Evolus rejected the request ({status}). {snippet}".strip()


class EvolusBlocks:
    def __init__(self, settings: Any, repo: Any, client: Any = None):
        self.s = settings
        self.repo = repo
        self._client = client  # tests inject a fake; otherwise a short-lived httpx client is used per call
        self._table_ready = False

    # ------------------------------------------------------------------ config
    @property
    def enabled(self) -> bool:
        return bool(getattr(self.s, "evolus_sentiment_enabled", False))

    @property
    def base_url(self) -> str:
        return (getattr(self.s, "evolus_rest_base_url", "") or "").rstrip("/")

    @property
    def tool(self) -> str:
        return getattr(self.s, "evolus_sentiment_tool", "sentiment") or "sentiment"

    @property
    def endpoint_host(self) -> str:
        return _host(self.base_url)

    def configured(self) -> tuple[bool, str | None]:
        missing = [n for n, v in (("EVOLUS_API_KEY", self.s.evolus_api_key), ("EVOLUS_APP_ID", self.s.evolus_app_id)) if not v]
        if missing:
            return False, f"Evolus AI Tool is not configured (missing {', '.join(missing)})."
        return True, None

    def _ensure_table(self) -> None:
        if self._table_ready:
            return
        self.repo.db.execute(TABLE_SQL)
        self.repo.db.execute(INDEX_SQL)
        self._table_ready = True

    # ------------------------------------------------------------------ the API call
    async def call_tool(self, text: str) -> dict:
        """One call to the Evolus AI Tool. Never raises. Returns a dict with ok, label, score, raw, error_*."""
        ok, why = self.configured()
        if not ok:
            return {"ok": False, "error_code": "missing_credentials", "error_message": why}
        url = f"{self.base_url}/ai-tools/{self.tool}/run"
        field = getattr(self.s, "evolus_tool_input_field", "text") or "text"
        payload: dict[str, Any] = dict(getattr(self.s, "evolus_tool_extra", None) or {})
        payload[field] = text[:MAX_TEXT]
        headers = {"X-ApiKey": self.s.evolus_api_key, "X-Application-Id": self.s.evolus_app_id,
                   "Content-Type": "application/json", "Accept": "application/json"}
        started = time.perf_counter()
        try:
            resp = await self._post(url, headers, payload)
        except asyncio.TimeoutError:
            return {"ok": False, "error_code": "timeout", "error_message": f"Evolus did not answer within {int(CALL_TIMEOUT_S)} seconds."}
        except Exception as exc:  # noqa: BLE001 - transport problems of any kind
            name = type(exc).__name__
            if "Timeout" in name:
                return {"ok": False, "error_code": "timeout", "error_message": f"Evolus did not answer within {int(CALL_TIMEOUT_S)} seconds."}
            return {"ok": False, "error_code": "unreachable",
                    "error_message": f"Could not reach Evolus ({name}). Check EVOLUS_REST_BASE_URL and your network."}
        latency = round((time.perf_counter() - started) * 1000)
        status = int(getattr(resp, "status_code", 0))
        if status >= 400:
            code, msg = _error_from_status(status, getattr(resp, "text", ""))
            return {"ok": False, "error_code": code, "error_message": msg, "http_status": status, "latency_ms": latency}
        try:
            data = resp.json()
        except ValueError:
            return {"ok": False, "error_code": "bad_response", "error_message": "Evolus returned something that is not JSON.",
                    "http_status": status, "latency_ms": latency}
        label, score = parse_tone(data)
        raw = json.dumps(data, ensure_ascii=False)[:1500]
        return {"ok": True, "label": label, "score": score, "raw": raw, "http_status": status, "latency_ms": latency}

    async def _post(self, url: str, headers: dict, payload: dict):
        if self._client is not None:
            return await asyncio.wait_for(self._client.post(url, headers=headers, json=payload), CALL_TIMEOUT_S)
        import httpx  # local import: only needed when the feature is switched on

        async with httpx.AsyncClient(timeout=httpx.Timeout(CALL_TIMEOUT_S, connect=10.0)) as c:
            return await c.post(url, headers=headers, json=payload)

    # ------------------------------------------------------------------ storing results
    def _store(self, campaign_id: str | None, experience_id: str | None, audience: str | None, field: str,
               text: str, res: dict) -> None:
        self.repo.db.execute(
            """INSERT INTO evolus_tone_checks(ts, campaign_id, experience_id, audience_name, field, text_preview, tool,
               endpoint_host, ok, label, score, latency_ms, http_status, error_code, error_message, raw)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (_now(), campaign_id, experience_id, audience, field, text[:120], self.tool, self.endpoint_host,
             1 if res.get("ok") else 0, res.get("label"), res.get("score"), res.get("latency_ms"), res.get("http_status"),
             res.get("error_code"), (res.get("error_message") or "")[:300] or None, res.get("raw")))

    # ------------------------------------------------------------------ whole campaign
    async def run_for_campaign(self, campaign_id: str) -> dict | None:
        """Run the Sentiment tool on the headline and the message of every ready audience. Never raises."""
        if not self.enabled:
            return None
        ok, _ = self.configured()
        if not ok:
            return self.summary(campaign_id)
        self._ensure_table()
        audiences = {a["id"]: a["name"] for a in self.repo.list_audiences(campaign_id)}
        jobs: list[tuple[dict, str, str]] = []
        for e in self.repo.list_experiences(campaign_id):
            if e.get("status") != "ready":
                continue
            for field in ("headline", "body_copy"):
                text = (e.get(field) or "").strip()
                if text:
                    jobs.append((e, field, text))
        sem = asyncio.Semaphore(CONCURRENCY)

        async def one(e: dict, field: str, text: str):
            async with sem:
                return e, field, text, await self.call_tool(text)

        results = await asyncio.gather(*(one(*j) for j in jobs))
        self.repo.db.execute("DELETE FROM evolus_tone_checks WHERE campaign_id = ?", (campaign_id,))
        for e, field, text, res in results:
            self._store(campaign_id, e["id"], audiences.get(e["audience_id"]), field, text, res)
        logger.info("tone check finished campaign=%s calls=%d ok=%d", campaign_id, len(results),
                    sum(1 for r in results if r[3].get("ok")))
        return self.summary(campaign_id)

    async def run_test(self) -> dict:
        """One tiny call for the Settings 'Test connection' button."""
        self._ensure_table()
        sample = "The new running shoe feels light and comfortable."
        res = await self.call_tool(sample)
        self._store(None, None, None, "test", sample, res)
        return res

    # ------------------------------------------------------------------ reading results
    def summary(self, campaign_id: str) -> dict | None:
        """What the campaign page shows. None when the feature is switched off (the card is then hidden)."""
        if not self.enabled:
            return None
        ok, why = self.configured()
        self._ensure_table()
        rows = self.repo.db.query(
            """SELECT ts, experience_id, audience_name, field, text_preview, ok, label, score, latency_ms,
                      error_code, error_message, raw FROM evolus_tone_checks WHERE campaign_id = ? ORDER BY id""",
            (campaign_id,))
        results = [{"experience_id": r["experience_id"], "audience": r["audience_name"], "field": r["field"],
                    "text_preview": r["text_preview"], "ok": bool(r["ok"]), "label": r["label"], "score": r["score"],
                    "latency_ms": r["latency_ms"], "error_code": r["error_code"], "error_message": r["error_message"],
                    "raw": r["raw"]} for r in rows]
        return {"enabled": True, "configured": ok, "problem": why, "tool": self.tool, "endpoint_host": self.endpoint_host,
                "ran_at": rows[-1]["ts"] if rows else None, "results": results,
                "counts": {"ok": sum(1 for r in results if r["ok"]), "failed": sum(1 for r in results if not r["ok"])},
                "note": NOTE}

    def evidence(self) -> dict:
        """Evidence for the Settings status card (all rows, including test calls)."""
        if not self.enabled:
            return {"calls_recorded": 0, "last_success": None, "has_real_success": False, "has_local_success": False,
                    "last_error": None, "error_is_latest": False}
        self._ensure_table()
        q = self.repo.db.query
        good = q("""SELECT ts, tool, endpoint_host, latency_ms FROM evolus_tone_checks WHERE ok=1 ORDER BY id DESC LIMIT 50""")
        bad = self.repo.db.one("""SELECT ts, error_code, error_message, endpoint_host FROM evolus_tone_checks
                                  WHERE ok=0 ORDER BY id DESC LIMIT 1""")
        total = self.repo.db.one("SELECT COUNT(*) n FROM evolus_tone_checks")["n"]
        real = [r for r in good if not _is_loopback(r["endpoint_host"] or "")]
        local = [r for r in good if _is_loopback(r["endpoint_host"] or "")]
        last_ok = (real or local or [None])[0]
        return {"calls_recorded": total,
                "last_success": ({**last_ok, "model": last_ok["tool"], "input_tokens": None, "output_tokens": None}
                                 if last_ok else None),
                "has_real_success": bool(real), "has_local_success": bool(local),
                "last_error": ({"ts": bad["ts"], "error_code": bad["error_code"], "error_message": bad["error_message"],
                                "endpoint_host": bad["endpoint_host"]} if bad else None),
                "error_is_latest": bool(bad and (not last_ok or bad["ts"] > last_ok["ts"]))}
