"""SQLite access and schema migrations (tracked with ``PRAGMA user_version``)."""
from __future__ import annotations

import contextlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Iterator, Sequence

MIGRATIONS: list[str] = [
    # ---- version 1: initial schema -------------------------------------------------------
    """
    CREATE TABLE brand_guide (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        name TEXT NOT NULL DEFAULT '',
        tone TEXT NOT NULL DEFAULT 'Confident',
        brand_values TEXT NOT NULL DEFAULT '',
        facts TEXT NOT NULL DEFAULT '[]',
        avoid TEXT NOT NULL DEFAULT '[]',
        updated_at TEXT
    );

    CREATE TABLE campaigns (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        brand TEXT NOT NULL,
        product TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        objective TEXT NOT NULL DEFAULT 'Brand awareness',
        tone TEXT NOT NULL DEFAULT 'Confident',
        brand_values TEXT NOT NULL DEFAULT '',
        language TEXT NOT NULL DEFAULT 'English',
        approved_discount TEXT,
        facts TEXT NOT NULL DEFAULT '[]',
        avoid TEXT NOT NULL DEFAULT '[]',
        status TEXT NOT NULL DEFAULT 'draft',
        status_reason TEXT NOT NULL DEFAULT '',
        strategy TEXT,
        review TEXT,
        product_image TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE audiences (
        id TEXT PRIMARY KEY,
        campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
        position INTEGER NOT NULL DEFAULT 0,
        name TEXT NOT NULL,
        interests TEXT NOT NULL DEFAULT '',
        needs TEXT NOT NULL DEFAULT '',
        angle TEXT NOT NULL DEFAULT 'Performance',
        context TEXT NOT NULL DEFAULT '',
        source TEXT NOT NULL DEFAULT 'user'
    );
    CREATE INDEX ix_audiences_campaign ON audiences(campaign_id, position);

    CREATE TABLE experiences (
        id TEXT PRIMARY KEY,
        campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
        audience_id TEXT NOT NULL UNIQUE REFERENCES audiences(id) ON DELETE CASCADE,
        status TEXT NOT NULL DEFAULT 'queued',
        headline TEXT NOT NULL DEFAULT '',
        body_copy TEXT NOT NULL DEFAULT '',
        cta TEXT NOT NULL DEFAULT '',
        concept TEXT NOT NULL DEFAULT '',
        rationale TEXT NOT NULL DEFAULT '',
        visual_direction TEXT NOT NULL DEFAULT '',
        hero_image_prompt TEXT NOT NULL DEFAULT '',
        scenes TEXT NOT NULL DEFAULT '[]',
        start_scene TEXT,
        provider TEXT,
        model TEXT,
        fallback_used INTEGER NOT NULL DEFAULT 0,
        attempts INTEGER NOT NULL DEFAULT 0,
        input_tokens INTEGER,
        output_tokens INTEGER,
        latency_ms INTEGER,
        cost_usd REAL,
        error TEXT,
        human_edited INTEGER NOT NULL DEFAULT 0,
        version INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX ix_experiences_campaign ON experiences(campaign_id);

    CREATE TABLE images (
        id TEXT PRIMARY KEY,
        experience_id TEXT NOT NULL REFERENCES experiences(id) ON DELETE CASCADE,
        scene_key TEXT NOT NULL,
        aspect_ratio TEXT NOT NULL,
        prompt TEXT NOT NULL,
        prompt_hash TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'queued',
        error_code TEXT,
        error_message TEXT,
        provider TEXT,
        model TEXT,
        file_name TEXT,
        mime TEXT,
        bytes INTEGER,
        sha256 TEXT,
        width INTEGER,
        height INTEGER,
        latency_ms INTEGER,
        cost_usd REAL,
        attempts INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX ix_images_experience ON images(experience_id);
    -- At most one queued/running generation per (experience, scene, aspect ratio): stops duplicate spend.
    CREATE UNIQUE INDEX ux_images_active ON images(experience_id, scene_key, aspect_ratio)
        WHERE status IN ('queued', 'running');

    CREATE TABLE jobs (
        id TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        target_key TEXT NOT NULL,
        campaign_id TEXT,
        experience_id TEXT,
        status TEXT NOT NULL DEFAULT 'queued',
        progress TEXT NOT NULL DEFAULT '{}',
        result TEXT,
        error TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE UNIQUE INDEX ux_jobs_active ON jobs(target_key) WHERE status IN ('queued', 'running');
    CREATE INDEX ix_jobs_campaign ON jobs(campaign_id);

    CREATE TABLE events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        type TEXT NOT NULL,
        campaign_id TEXT,
        experience_id TEXT,
        audience_name TEXT,
        scene_id TEXT,
        choice_id TEXT,
        created_at TEXT NOT NULL
    );
    CREATE INDEX ix_events_type ON events(type, experience_id);

    CREATE TABLE provider_calls (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        purpose TEXT NOT NULL,
        provider TEXT NOT NULL,
        model TEXT,
        endpoint_host TEXT,
        ok INTEGER NOT NULL,
        error_code TEXT,
        error_message TEXT,
        latency_ms INTEGER,
        input_tokens INTEGER,
        output_tokens INTEGER,
        cost_usd REAL,
        request_id TEXT,
        campaign_id TEXT,
        experience_id TEXT,
        attempt INTEGER,
        fallback_used INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX ix_calls_provider ON provider_calls(provider, ok, ts);
    CREATE INDEX ix_calls_campaign ON provider_calls(campaign_id);
    """,
    # ---- version 2: translations of an experience (Gemma main, Evolus fallback) ----------------
    """
    CREATE TABLE translations (
        id TEXT PRIMARY KEY,
        experience_id TEXT NOT NULL REFERENCES experiences(id) ON DELETE CASCADE,
        language TEXT NOT NULL,
        headline TEXT NOT NULL,
        body_copy TEXT NOT NULL,
        cta TEXT NOT NULL,
        scenes TEXT NOT NULL DEFAULT '[]',
        source_hash TEXT NOT NULL,
        checks TEXT NOT NULL DEFAULT '[]',
        provider TEXT,
        model TEXT,
        fallback_used INTEGER NOT NULL DEFAULT 0,
        attempts INTEGER NOT NULL DEFAULT 0,
        input_tokens INTEGER,
        output_tokens INTEGER,
        latency_ms INTEGER,
        cost_usd REAL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE (experience_id, language)
    );
    CREATE INDEX ix_translations_experience ON translations(experience_id);
    """,
]


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 8000")
        try:
            conn.execute("PRAGMA journal_mode = WAL")
        except sqlite3.DatabaseError:
            pass
        return conn

    def migrate(self) -> int:
        conn = self.connect()
        try:
            current = conn.execute("PRAGMA user_version").fetchone()[0]
            for version, script in enumerate(MIGRATIONS, start=1):
                if version <= current:
                    continue
                conn.execute("BEGIN IMMEDIATE")
                try:
                    for statement in _split_script(script):
                        conn.execute(statement)
                    conn.execute(f"PRAGMA user_version = {version}")
                    conn.execute("COMMIT")
                except Exception:
                    conn.execute("ROLLBACK")
                    raise
            return len(MIGRATIONS)
        finally:
            conn.close()

    @contextlib.contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """One transaction. Commits on success, rolls back on any exception."""
        conn = self.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            else:
                conn.execute("COMMIT")
        finally:
            conn.close()

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[dict]:
        conn = self.connect()
        try:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]
        finally:
            conn.close()

    def one(self, sql: str, params: Sequence[Any] = ()) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        with self.tx() as conn:
            return conn.execute(sql, params).rowcount

    def check(self) -> bool:
        try:
            return self.query("SELECT 1 AS ok")[0]["ok"] == 1
        except sqlite3.Error:
            return False


def _split_script(script: str) -> list[str]:
    """Split on semicolons that end a statement (our migrations contain no semicolons in strings)."""
    lines = [ln for ln in script.splitlines() if not ln.strip().startswith("--")]
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def loads(value: str | None, default: Any = None) -> Any:
    if value is None or value == "":
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default
