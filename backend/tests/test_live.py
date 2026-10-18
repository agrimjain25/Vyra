"""Tests against REAL provider APIs. Skipped unless RUN_LIVE_TESTS=1 and real keys are in backend/.env.

Run:  RUN_LIVE_TESTS=1 python -m pytest -m live -s     (PowerShell: $env:RUN_LIVE_TESTS=1; python -m pytest -m live -s)
These cost a few cents of API usage. Nothing here is mocked.
"""
import os
import time
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from conftest import campaign_payload, wait_job

pytestmark = [pytest.mark.live, pytest.mark.skipif(os.environ.get("RUN_LIVE_TESTS") != "1", reason="set RUN_LIVE_TESTS=1 to call real APIs")]


@pytest.fixture(scope="module")
def live_client(tmp_path_factory):
    from app.config import Settings
    from app.main import create_app
    t = tmp_path_factory.mktemp("live")
    s = Settings.from_env().model_copy(update={"database_path": t / "live.db", "media_dir": t / "media", "rate_limit_per_minute": 1000})
    with TestClient(create_app(s)) as c:
        yield c


def test_live_generation_returns_validated_distinct_experiences(live_client):
    cid = live_client.post("/api/campaigns", json=campaign_payload()).json()["id"]
    job = wait_job(live_client, live_client.post(f"/api/campaigns/{cid}/generate", json={}).json()["job"]["id"], timeout=600)
    assert job["status"] == "succeeded", job
    d = live_client.get(f"/api/campaigns/{cid}").json()
    assert all(e["status"] == "ready" for e in d["experiences"])
    assert len({e["headline"] for e in d["experiences"]}) == len(d["experiences"])
    for e in d["experiences"]:
        print(e["audience"]["name"], "|", e["headline"], "|", e["generation"])


def test_live_image_is_real_and_retrievable(live_client):
    d = live_client.get("/api/campaigns").json()["campaigns"][0]
    full = live_client.get(f"/api/campaigns/{d['id']}").json()
    e = full["experiences"][0]
    r = live_client.post(f"/api/experiences/{e['id']}/images", json={"scope": "hero"})
    assert wait_job(live_client, r.json()["job"]["id"], timeout=600)["status"] == "succeeded"
    img = live_client.get(f"/api/experiences/{e['id']}").json()["images"][0]
    body = live_client.get(img["url"])
    assert body.status_code == 200 and len(body.content) > 5_000 and img["width"]
