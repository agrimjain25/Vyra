import pytest
from conftest import campaign_payload, create_and_generate, wait_job, set_mock
import mock_providers as mp


def test_health_reports_providers_honestly(client):
    h = client.get("/api/health").json()
    assert h["status"] == "ok" and h["database"]["ok"] is True
    assert h["llm"]["primary"] == "amd"
    assert h["llm"]["chain"][0]["configured"] is True and h["llm"]["chain"][0]["model"] == "mock-amd-model"
    # nothing has been called yet, so nothing may be reported as verified
    assert h["integrations"]["amd"] == "configured"
    assert h["integrations"]["pwc_italy"] == "not_implemented"


def test_health_unconfigured(tmp_path, mock_server):
    from conftest import make_settings
    from app.main import create_app
    from starlette.testclient import TestClient
    s = make_settings(tmp_path, mock_server, amd_api_key=None, gemma_api_key=None, pollinations_api_key=None)
    with TestClient(create_app(s)) as c:
        h = c.get("/api/health").json()
        assert h["integrations"]["amd"] == "needs_credentials"
        assert h["image"]["configured"] is False
        r = c.post("/api/campaigns", json=campaign_payload()).json()
        g = c.post(f"/api/campaigns/{r['id']}/generate", json={})
        assert g.status_code == 503
        assert g.json()["error"]["code"] == "no_provider_configured"
        assert "AMD_API_KEY" in g.json()["error"]["message"] or "AMD" in g.json()["error"]["message"]


def test_create_campaign_persists_and_returns_detail(client):
    r = client.post("/api/campaigns", json=campaign_payload())
    assert r.status_code == 201
    d = r.json()
    assert d["status"] == "draft" and len(d["audiences"]) == 3 and d["experiences"] == []
    assert client.get(f"/api/campaigns/{d['id']}").json()["brand"] == "Northwind Athletics"
    assert len(client.get("/api/campaigns").json()["campaigns"]) == 1


@pytest.mark.parametrize("patch,field", [
    ({"brand": ""}, "brand"), ({"product": ""}, "product"), ({"description": ""}, "description"),
    ({"tone": "Angry"}, "tone"), ({"audiences": []}, "audiences"),
    ({"audiences": [{"name": "A", "needs": "x"}] * 2}, "audiences"),
    ({"audiences": [{"name": f"A{i}", "needs": "x"} for i in range(6)]}, "audiences"),
    ({"audiences": [{"name": "A", "needs": ""}]}, "audiences"),
])
def test_invalid_campaigns_rejected(client, patch, field):
    r = client.post("/api/campaigns", json=campaign_payload(**patch))
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "validation_error"
    assert field in r.json()["error"]["message"]


def test_bad_json_and_404_are_json(client):
    r = client.post("/api/campaigns", content=b"{nope", headers={"content-type": "application/json"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_json"
    assert client.get("/api/campaigns/doesnotexist").json()["error"]["code"] == "not_found"
    assert client.get("/api/nothing").status_code == 404


def test_generation_end_to_end_mocked(client, mock_server):
    out = create_and_generate(client)
    assert out["job"]["status"] == "succeeded", out["job"]
    d = out["detail"]
    assert [e["status"] for e in d["experiences"]] == ["ready"] * 3
    heads = {e["headline"] for e in d["experiences"]}
    assert len(heads) == 3
    e = d["experiences"][0]
    assert e["generation"]["provider"] == "amd" and e["generation"]["model"] == "mock-amd-model"
    assert e["generation"]["fallback_used"] is False and e["generation"]["input_tokens"] > 0
    assert d["strategy"]["core_idea"]
    assert d["review"] is not None and d["status"] in ("ready", "needs_review")
    assert d["usage"]["calls"] >= 5 and d["usage"]["cost_usd"] is None   # no prices configured -> unknown, not invented
    h = client.get("/api/health").json()
    assert h["integrations"]["amd"] == "local_test_endpoint"   # honest: success only against loopback
