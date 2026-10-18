"""Gemma (Google open model): translation main, Evolus fallback; Gemma as review/generation fallback. Mocked endpoints."""
import httpx
import pytest
from starlette.testclient import TestClient

import mock_providers as mp
from app.config import Settings
from app.main import create_app
from conftest import create_and_generate, make_settings, set_mock

EVOLUS = dict(evolus_api_key=mp.AMD_KEY, evolus_app_id="app1", evolus_agent_id="agent1")


def _calls(mock_server):
    return httpx.get(f"{mock_server.base}/__calls", trust_env=False).json()


def _first_exp(out):
    return out["detail"]["experiences"][0]


def _translate(c, eid, *langs):
    return c.post(f"/api/experiences/{eid}/translate", json={"languages": list(langs)})


def test_gemma_translates_and_keeps_story_structure(client):
    out = create_and_generate(client)
    e = _first_exp(out)
    r = _translate(client, e["id"], "Italian")
    assert r.status_code == 200, r.text
    res = r.json()["results"][0]
    assert res["ok"] is True
    t = res["translation"]
    assert t["generation"]["provider"] == "gemma" and t["generation"]["fallback_used"] is False
    assert t["headline"].startswith("[IT] ") and t["stale"] is False
    assert [s["id"] for s in t["scenes"]] == [s["id"] for s in e["scenes"]]
    listed = client.get(f"/api/experiences/{e['id']}/translations").json()["translations"]
    assert len(listed) == 1 and listed[0]["language"] == "Italian"
    by_campaign = client.get(f"/api/campaigns/{out['id']}/translations").json()["translations"]
    assert e["id"] in by_campaign
    # Translating again replaces the saved translation instead of adding a second one.
    _translate(client, e["id"], "Italian")
    assert len(client.get(f"/api/experiences/{e['id']}/translations").json()["translations"]) == 1


def test_translation_goes_stale_when_the_original_is_edited(client):
    e = _first_exp(create_and_generate(client))
    _translate(client, e["id"], "Spanish")
    client.patch(f"/api/experiences/{e['id']}", json={"headline": "A brand new headline"})
    t = client.get(f"/api/experiences/{e['id']}/translations").json()["translations"][0]
    assert t["stale"] is True


def test_same_language_is_rejected(client):
    e = _first_exp(create_and_generate(client))
    r = _translate(client, e["id"], "English")
    assert r.status_code == 400 and r.json()["error"]["code"] == "same_language"


def test_evolus_translates_when_gemma_fails(tmp_path, mock_server):
    s = make_settings(tmp_path, mock_server, **EVOLUS)
    with TestClient(create_app(s)) as c:
        e = _first_exp(create_and_generate(c))
        set_mock(mock_server, "gemma", "http500", -1)
        r = _translate(c, e["id"], "French")
        assert r.status_code == 200, r.text
        t = r.json()["results"][0]["translation"]
        assert t["generation"]["provider"] == "evolus" and t["generation"]["fallback_used"] is True
        usage = c.get("/api/analytics/summary").json()["usage"]
        assert any(u["purpose"] == "translate" and u["provider"] == "evolus" and u["ok_calls"] for u in usage)
        assert any(u["purpose"] == "translate" and u["provider"] == "gemma" and not u["ok_calls"] for u in usage)


def test_translation_that_breaks_the_story_structure_is_not_accepted(tmp_path, mock_server):
    s = make_settings(tmp_path, mock_server, **EVOLUS)
    with TestClient(create_app(s)) as c:
        e = _first_exp(create_and_generate(c))
        set_mock(mock_server, "gemma", "bad_ids", -1)
        r = _translate(c, e["id"], "German")
        assert r.status_code == 200, r.text
        assert r.json()["results"][0]["translation"]["generation"]["provider"] == "evolus"


def test_translation_error_is_clear_when_both_fail(client, mock_server):
    e = _first_exp(create_and_generate(client))
    set_mock(mock_server, "gemma", "http500", -1)
    r = _translate(client, e["id"], "Hindi")
    assert r.status_code == 502
    assert "Gemma" in r.json()["error"]["message"]
    assert client.get(f"/api/experiences/{e['id']}/translations").json()["translations"] == []


def test_translation_needs_a_configured_provider(tmp_path, mock_server):
    s = make_settings(tmp_path, mock_server)
    with TestClient(create_app(s)) as c:
        e = _first_exp(create_and_generate(c))
    s2 = make_settings(tmp_path / "b", mock_server, gemma_api_key=None, database_path=s.database_path)
    with TestClient(create_app(s2)) as c2:
        r = _translate(c2, e["id"], "Italian")
        assert r.status_code == 503 and r.json()["error"]["code"] == "no_provider_configured"


@pytest.mark.parametrize("mode,flag", [("reject_system", "system"), ("reject_json", "json")])
def test_gemma_retries_without_features_the_api_rejects(client, mock_server, mode, flag):
    e = _first_exp(create_and_generate(client))
    set_mock(mock_server, "gemma", mode, -1)
    r = _translate(client, e["id"], "Italian")
    assert r.status_code == 200, r.text
    assert r.json()["results"][0]["translation"]["generation"]["provider"] == "gemma"
    gemma = [x for x in _calls(mock_server) if x["provider"] == "gemma"]
    assert gemma[-1][flag] is False and any(x[flag] for x in gemma)  # first tried with it, then without


def test_routing_chains(tmp_path, mock_server):
    s = make_settings(tmp_path, mock_server, primary_llm="amd", fallback_llm="gemma", review_llm="evolus", **EVOLUS)
    with TestClient(create_app(s)) as c:
        hub = c.app.state.vyra.hub
        assert hub.chain_names("review") == ["evolus", "gemma", "amd"]
        assert hub.chain_names("translate") == ["gemma", "evolus_translate"]
        assert hub.chain_names("experience") == ["amd", "gemma"]
        assert hub.chain_names("strategy") == ["amd", "gemma"]


def test_gemma_reviews_when_evolus_fails(tmp_path, mock_server):
    s = make_settings(tmp_path, mock_server, fallback_llm="gemma", review_llm="evolus", llm_max_retries=0, **EVOLUS)
    with TestClient(create_app(s)) as c:
        set_mock(mock_server, "evolus", "http500", -1)
        out = create_and_generate(c)
        assert out["job"]["status"] == "succeeded", out["job"]
        c.post(f"/api/campaigns/{out['id']}/review", json={"ai": True})
        usage = c.get("/api/analytics/summary").json()["usage"]
        assert any(u["purpose"] == "review" and u["provider"] == "gemma" and u["ok_calls"] for u in usage)


def test_legacy_google_provider_name_means_gemma():
    assert Settings(fallback_llm="google").fallback_llm == "gemma"


def test_integrations_list_gemma_and_no_gemini(client):
    body = client.get("/api/integrations").json()
    items = body["integrations"] if isinstance(body, dict) and "integrations" in body else body
    ids = [i["id"] for i in items]
    assert "gemma" in ids and "google_text" not in ids
    assert "Gemini" not in " ".join(i["name"] for i in items)
    r = client.post("/api/integrations/gemma/test").json()
    assert r["ok"] is True and r["returned_valid_json"] is True


def test_review_findings_name_the_provider_that_really_answered(tmp_path, mock_server):
    s = make_settings(tmp_path, mock_server, fallback_llm="gemma", review_llm="evolus", llm_max_retries=0, **EVOLUS)
    with TestClient(create_app(s)) as c:
        set_mock(mock_server, "evolus", "http500", -1)      # Evolus fails, so Gemma must be named, never Evolus
        out = create_and_generate(c)
        c.post(f"/api/campaigns/{out['id']}/review", json={"ai": True})
        ai = c.get(f"/api/campaigns/{out['id']}").json()["review"]["ai"]
        assert ai["status"] == "ran" and ai["provider"] == "gemma" and ai["fallback_used"] is True


def test_gemma_through_an_openai_compatible_endpoint_like_token_factory(tmp_path, mock_server):
    """Route 1: Gemma served by the AMD endpoint (OpenAI-compatible). The Google API is not called at all."""
    s = make_settings(tmp_path, mock_server, gemma_openai_base_url=f"{mock_server.base}/gemma/v1",
                      gemma_openai_api_key=mp.AMD_KEY, gemma_model="Gemma-4-31B-IT")
    with TestClient(create_app(s)) as c:
        e = _first_exp(create_and_generate(c))
        r = _translate(c, e["id"], "Hindi")
        assert r.status_code == 200, r.text
        t = r.json()["results"][0]["translation"]
        assert t["generation"]["provider"] == "gemma" and t["generation"]["fallback_used"] is False
        assert t["generation"]["model"] == "Gemma-4-31B-IT"
        assert not [x for x in _calls(mock_server) if x["provider"] == "gemma" and x.get("key_header")]  # Google route unused
        card = next(i for i in c.get("/api/integrations").json()["integrations"] if i["id"] == "gemma")
        assert card["details"]["route"] in ("self-hosted (OpenAI-compatible)", "AMD endpoint (OpenAI-compatible)")
        assert c.post("/api/integrations/gemma/test").json()["ok"] is True
