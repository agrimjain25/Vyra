"""Security, persistence, brand guide, editing, analytics and logging."""
import io
import logging

import pytest
from starlette.testclient import TestClient
from conftest import campaign_payload, create_and_generate, make_settings, wait_job, set_mock
from app.main import create_app
from app.logging_setup import Redactor


def test_campaigns_and_images_survive_restart(tmp_path, mock_server):
    s = make_settings(tmp_path, mock_server)
    with TestClient(create_app(s)) as c:
        out = create_and_generate(c)
        e = out["detail"]["experiences"][0]
        wait_job(c, c.post(f"/api/experiences/{e['id']}/images", json={"scope": "hero"}).json()["job"]["id"])
        before = c.get(f"/api/campaigns/{out['id']}").json()
        c.put("/api/brand", json={"name": "Northwind", "tone": "Warm", "values": "kind", "facts": ["f1"], "avoid": ["x"]})
    with TestClient(create_app(s)) as c2:          # a brand-new process and app object, same files
        after = c2.get(f"/api/campaigns/{out['id']}").json()
        assert after["experiences"][0]["headline"] == before["experiences"][0]["headline"]
        assert after["review"] == before["review"] and after["status"] == before["status"]
        url = after["experiences"][0]["hero_url"]
        assert url and c2.get(url).status_code == 200
        assert c2.get("/api/brand").json()["tone"] == "Warm"


def test_restart_marks_interrupted_jobs_failed(tmp_path, mock_server):
    s = make_settings(tmp_path, mock_server)
    with TestClient(create_app(s)) as c:
        cid = c.post("/api/campaigns", json=campaign_payload()).json()["id"]
        set_mock(mock_server, "amd", "slow", 1)
        job = c.post(f"/api/campaigns/{cid}/generate", json={}).json()["job"]
    # simulate a crash: the old process died leaving the row "running"; a new app starts on the same database
    import sqlite3
    db = sqlite3.connect(s.database_path)
    db.execute("UPDATE jobs SET status='running', error=NULL"); db.commit(); db.close()
    with TestClient(create_app(s)) as c2:
        j = c2.get(f"/api/jobs/{job['id']}").json()
        assert j["status"] == "failed" and j["error"]["code"] == "interrupted"


def test_duplicate_generate_requests_share_one_job(client, mock_server):
    cid = client.post("/api/campaigns", json=campaign_payload()).json()["id"]
    set_mock(mock_server, "amd", "slow", 1)
    a = client.post(f"/api/campaigns/{cid}/generate", json={}).json()
    b = client.post(f"/api/campaigns/{cid}/generate", json={}).json()
    assert b["already_running"] and b["job"]["id"] == a["job"]["id"]
    wait_job(client, a["job"]["id"])
    assert client.get(f"/api/campaigns/{cid}").json()["usage"]["calls"] >= 4


def test_edit_marks_content_stale_only_when_relevant(client):
    out = create_and_generate(client)
    cid = out["id"]
    d = out["detail"]
    payload = campaign_payload()
    payload["audiences"] = [{**a, "id": x["id"]} for a, x in zip(payload["audiences"], d["audiences"])]
    same = client.put(f"/api/campaigns/{cid}", json={**payload, "name": "Renamed only"}).json()
    assert same["stale"] is False and same["campaign"]["name"] == "Renamed only" and same["campaign"]["status"] != "draft"
    payload["facts"] = ["Cushioned midsole"]
    r = client.put(f"/api/campaigns/{cid}", json=payload).json()
    assert r["stale"] is True and r["campaign"]["status"] == "draft"
    assert {e["status"] for e in r["campaign"]["experiences"]} == {"stale"}
    assert "Regenerate" in r["campaign"]["status_reason"]
    bad = client.put(f"/api/campaigns/{cid}", json={**payload, "audiences": [{**payload["audiences"][0], "id": "not-mine"}]})
    assert bad.status_code == 400


def test_duplicate_campaign(client):
    out = create_and_generate(client)
    r = client.post(f"/api/campaigns/{out['id']}/duplicate")
    assert r.status_code == 201 and r.json()["name"].endswith("(copy)") and r.json()["experiences"] == []
    assert len(r.json()["audiences"]) == 3 and r.json()["status"] == "draft"


def test_brand_guide(client):
    assert client.get("/api/brand").json()["name"] == ""
    r = client.put("/api/brand", json={"name": " ACME ", "tone": "Playful", "values": "v", "facts": ["a", "A", " "], "avoid": ["x"]})
    assert r.status_code == 200 and r.json()["name"] == "ACME" and r.json()["facts"] == ["a"]
    assert client.put("/api/brand", json={"name": ""}).status_code == 422
    assert client.delete("/api/brand").status_code == 200 and client.get("/api/brand").json()["name"] == ""


def test_empty_analytics_has_no_fake_numbers(client):
    a = client.get("/api/analytics/summary").json()
    assert a["has_data"] is False and a["usage"] == [] and a["cost"]["known_usd"] is None
    assert a["viewer"]["has_events"] is False


def test_analytics_after_generation(client):
    create_and_generate(client)
    a = client.get("/api/analytics/summary").json()
    assert a["experiences"]["ready"] == 3 and a["usage"] and a["usage"][0]["provider"] == "amd"
    assert a["cost"]["known_usd"] is None   # prices not configured -> not invented


def test_token_auth(tmp_path, mock_server):
    s = make_settings(tmp_path, mock_server, api_token="secret-token-123")
    with TestClient(create_app(s)) as c:
        assert c.get("/api/health").status_code == 200
        assert c.get("/api/campaigns").status_code == 401
        assert c.get("/api/campaigns", headers={"X-Vyra-Token": "wrong"}).status_code == 401
        assert c.get("/api/campaigns", headers={"X-Vyra-Token": "secret-token-123"}).status_code == 200
        assert c.get("/api/campaigns", headers={"Authorization": "Bearer secret-token-123"}).status_code == 200
        assert c.get("/api/health").json()["auth_required"] is True
        assert c.get("/").status_code == 200    # the page itself is public; the data is not


def test_body_size_limits(tmp_path, mock_server):
    s = make_settings(tmp_path, mock_server, max_body_bytes=2048, max_json_bytes=1024)
    with TestClient(create_app(s)) as c:
        assert c.post("/api/campaigns", content=b"x" * 5000, headers={"content-type": "application/json"}).status_code == 413
        big = campaign_payload(description="d" * 700, values="v" * 400)
        assert c.post("/api/campaigns", json=big).status_code in (413, 422)


def test_rate_limit_on_expensive_endpoints(tmp_path, mock_server):
    s = make_settings(tmp_path, mock_server, rate_limit_per_minute=3)
    with TestClient(create_app(s)) as c:
        cid = c.post("/api/campaigns", json=campaign_payload()).json()["id"]
        codes = [c.post(f"/api/campaigns/{cid}/review", json={"ai": False}).status_code for _ in range(5)]
        assert codes[:3] == [202, 202, 202] or 202 in codes[:3]
        assert codes[-1] == 429
        r = c.post(f"/api/campaigns/{cid}/review", json={"ai": False})
        assert r.json()["error"]["code"] == "rate_limited" and "retry_after_seconds" in r.json()["error"]["details"]
        assert c.get("/api/campaigns").status_code == 200     # cheap reads are not limited


def test_cors_allows_only_configured_origins(client):
    ok = client.options("/api/campaigns", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173"
    bad = client.options("/api/campaigns", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in bad.headers


def test_security_headers_and_openapi(client):
    r = client.get("/api/health")
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["cache-control"] == "no-store" and r.headers["x-request-id"]
    spec = client.get("/api/openapi.json").json()
    assert spec["openapi"].startswith("3") and "/api/campaigns/{cid}/generate" in spec["paths"]
    assert "CampaignIn" in spec["components"]["schemas"]
    assert client.get("/api/docs").status_code == 200


def test_frontend_is_served_from_whitelist_only(client):
    assert client.get("/").status_code == 200 and "VYRA" in client.get("/").text
    assert client.get("/config.js").status_code == 200
    for p in ["/backend/.env", "/.env", "/backend/app/config.py", "/README.md"]:
        assert client.get(p).status_code == 404


def test_logs_never_contain_secrets(tmp_path, mock_server, caplog):
    s = make_settings(tmp_path, mock_server)
    r = Redactor(s.secret_values())
    rec = logging.LogRecord("x", logging.INFO, "f", 1, f"key {s.amd_api_key} and AIzaSyA1234567890abcdefghijkl and Bearer abcdefghijklmnop", (), None)
    r.filter(rec)
    out = rec.getMessage()
    assert s.amd_api_key not in out and "AIzaSy" not in out and "abcdefghijklmnop" not in out and "***" in out


def test_api_never_returns_secrets(client, settings):
    blob = "".join(client.get(p).text for p in ["/api/health", "/api/integrations", "/api/analytics/summary"])
    for secret in settings.secret_values():
        assert secret not in blob
