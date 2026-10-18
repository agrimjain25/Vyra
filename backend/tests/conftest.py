import sys
import time
from pathlib import Path

import pytest
from starlette.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import mock_providers as mp  # noqa: E402
from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402


@pytest.fixture(scope="session")
def mock_server():
    srv = mp.MockServer().start()
    yield srv
    srv.stop()


@pytest.fixture(autouse=True)
def _reset_mock(mock_server):
    mp.reset()
    yield


def make_settings(tmp_path: Path, mock_server, **over) -> Settings:
    base = dict(
        database_path=tmp_path / "vyra.db", media_dir=tmp_path / "media",
        frontend_dir=Path(__file__).resolve().parent.parent.parent,
        amd_base_url=f"{mock_server.base}/amd/v1", amd_api_key=mp.AMD_KEY, amd_model="mock-amd-model",
        gemma_api_key=mp.GOOGLE_KEY, gemma_api_base=f"{mock_server.base}/google/v1beta",
        gemma_model="mock-gemma-text",
        cf_account_id=mp.CF_ACCOUNT, cf_api_token=mp.CF_TOKEN, cf_base_url=f"{mock_server.base}/cloudflare", cf_image_model="mock-cf-flux",
        pollinations_api_key=mp.POLLINATIONS_KEY, pollinations_base_url=f"{mock_server.base}/pollinations",
        pollinations_model="mock-flux",
        image_provider="pollinations", primary_llm="amd", fallback_llm="none", llm_max_retries=2, llm_timeout_s=20, image_timeout_s=20,
        rate_limit_per_minute=1000, evolus_base_url=f"{mock_server.base}/evolus/v1",
    )
    base.update(over)
    return Settings(**base)


@pytest.fixture
def settings(tmp_path, mock_server):
    return make_settings(tmp_path, mock_server)


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as c:
        yield c


def campaign_payload(**over):
    p = {
        "name": "Stride Launch", "brand": "Northwind Athletics", "product": "Stride 4 running shoe",
        "description": "A lightweight everyday running shoe.", "objective": "Product launch", "tone": "Confident",
        "values": "honest, active", "language": "English", "approved_discount": None,
        "facts": ["Cushioned midsole", "Recycled mesh upper (verified)"], "avoid": ["guaranteed", "fastest"],
        "audiences": [
            {"name": "The Athlete", "interests": "running, training", "needs": "performance and comfort", "angle": "Performance"},
            {"name": "The Trendsetter", "interests": "style, streetwear", "needs": "self-expression", "angle": "Style"},
            {"name": "The Everyday Mover", "interests": "commuting, errands", "needs": "all-day versatility", "angle": "Everyday Comfort"},
        ],
    }
    p.update(over)
    return p


def wait_job(client, job_id: str, timeout: float = 30.0) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        j = client.get(f"/api/jobs/{job_id}").json()
        if j["status"] in ("succeeded", "failed"):
            return j
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish: {j}")


def create_and_generate(client, **over) -> dict:
    r = client.post("/api/campaigns", json=campaign_payload(**over))
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    g = client.post(f"/api/campaigns/{cid}/generate", json={})
    assert g.status_code == 202, g.text
    job = wait_job(client, g.json()["job"]["id"])
    return {"id": cid, "job": job, "detail": client.get(f"/api/campaigns/{cid}").json()}


def set_mock(mock_server, provider: str, mode: str, count: int = 1):
    import httpx
    httpx.post(f"{mock_server.base}/__control", json={"provider": provider, "mode": mode, "count": count}, trust_env=False)
