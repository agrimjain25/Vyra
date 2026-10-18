"""Provider reliability: retries, fallback, clear errors. (Mocked provider endpoints.)"""
from conftest import create_and_generate, make_settings, set_mock, campaign_payload, wait_job
from starlette.testclient import TestClient
from app.main import create_app
import mock_providers as mp


def test_malformed_json_is_retried_then_succeeds(client, mock_server):
    set_mock(mock_server, "amd", "malformed", 2)   # first two replies are not JSON
    out = create_and_generate(client)
    assert out["job"]["status"] == "succeeded"
    assert all(e["status"] == "ready" for e in out["detail"]["experiences"])


def test_json_wrapped_in_think_and_fences_is_accepted(client, mock_server):
    set_mock(mock_server, "amd", "fenced", 3)
    out = create_and_generate(client)
    assert out["job"]["status"] == "succeeded"


def test_invalid_story_graph_triggers_retry(client, mock_server):
    set_mock(mock_server, "amd", "invalid_graph", 1)
    out = create_and_generate(client)
    assert out["job"]["status"] == "succeeded"
    assert max(e["generation"]["attempts"] for e in out["detail"]["experiences"]) >= 2


def test_transient_errors_retry(client, mock_server):
    set_mock(mock_server, "amd", "http500", 2)
    out = create_and_generate(client)
    assert out["job"]["status"] == "succeeded"


def test_auth_failure_gives_clear_message_and_no_mock_content(tmp_path, mock_server):
    s = make_settings(tmp_path, mock_server, amd_api_key="wrong-key-123")
    with TestClient(create_app(s)) as c:
        out = create_and_generate(c)
        assert out["job"]["status"] == "failed"
        err = out["job"]["error"]
        assert err["code"] == "auth_failed" and "API key" in err["message"]
        d = out["detail"]
        assert d["status"] == "draft" and all(e["status"] == "failed" for e in d["experiences"])
        assert all(e["headline"] == "" for e in d["experiences"])  # nothing silently invented
        assert c.get("/api/health").json()["integrations"]["amd"] == "error"


def test_quota_exhausted_is_reported(client, mock_server):
    set_mock(mock_server, "amd", "quota", -1)
    out = create_and_generate(client)
    assert out["job"]["status"] == "failed" and out["job"]["error"]["code"] == "quota_exhausted"
    assert "quota" in out["job"]["error"]["message"].lower()


def test_fallback_provider_used_and_flagged(tmp_path, mock_server):
    s = make_settings(tmp_path, mock_server, fallback_llm="gemma", llm_max_retries=0)
    with TestClient(create_app(s)) as c:
        set_mock(mock_server, "amd", "http500", -1)
        out = create_and_generate(c)
        assert out["job"]["status"] == "succeeded", out["job"]
        gens = [e["generation"] for e in out["detail"]["experiences"]]
        assert all(g["provider"] == "gemma" and g["fallback_used"] is True for g in gens)
        assert gens[0]["model"] == "mock-gemma-text"


def test_gemma_as_primary_uses_generate_content_and_ignores_thought_parts(tmp_path, mock_server):
    s = make_settings(tmp_path, mock_server, primary_llm="gemma")
    with TestClient(create_app(s)) as c:
        out = create_and_generate(c)
        assert out["job"]["status"] == "succeeded", out["job"]
        assert out["detail"]["experiences"][0]["generation"]["provider"] == "gemma"
        calls = [x for x in __import__("httpx").get(f"{mock_server.base}/__calls", trust_env=False).json() if x["provider"] == "gemma"]
        assert calls and all(x["key_header"] == mp.GOOGLE_KEY and not x["image"] for x in calls)


def test_cost_only_when_prices_configured(tmp_path, mock_server):
    s = make_settings(tmp_path, mock_server, amd_price_in_per_mtok=1.0, amd_price_out_per_mtok=2.0)
    with TestClient(create_app(s)) as c:
        d = create_and_generate(c)["detail"]
        assert d["usage"]["cost_usd"] is not None and d["usage"]["cost_usd"] > 0
        assert d["experiences"][0]["cost"]["usd"] is not None


def test_integration_test_endpoint(client, mock_server):
    r = client.post("/api/integrations/amd/test").json()
    assert r["ok"] is True and r["returned_valid_json"] is True and r["input_tokens"] > 0
    set_mock(mock_server, "amd", "http401", 1)
    r = client.post("/api/integrations/amd/test").json()
    assert r["ok"] is False and r["error"]["code"] == "auth_failed"
    assert client.post("/api/integrations/nonsense/test").status_code == 404
