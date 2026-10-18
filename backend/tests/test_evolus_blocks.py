"""Tests for the optional Evolus AI Tool (Sentiment) block and the AMD proof summary.

Everything here uses local fakes: no real Evolus or AMD service is called. These tests show the application logic is
correct; they do not show that the real Evolus Sentiment tool accepts the assumed request body.
"""
import asyncio
import json
from types import SimpleNamespace

import pytest

from app.db import Database
from app.repo import Repo
from app.services import amd_proof
from app.services.evolus_blocks import EvolusBlocks, parse_tone


class FakeResp:
    def __init__(self, status=200, data=None, text=""):
        self.status_code, self._data, self.text = status, data, text

    def json(self):
        if self._data is None:
            raise ValueError("not json")
        return self._data


class FakeClient:
    """Stands in for httpx.AsyncClient; records every call."""

    def __init__(self, handler=None):
        self.calls = []
        self.handler = handler or (lambda url, headers, body: FakeResp(200, {"sentiment": "positive", "score": 0.91}))

    async def post(self, url, headers=None, json=None):
        self.calls.append({"url": url, "headers": headers, "json": json})
        return self.handler(url, headers, json)


def make_settings(**over):
    base = dict(evolus_api_key="k-test-evolus", evolus_app_id="app-test", evolus_agent_id="agent-test",
                evolus_rest_base_url="https://evolus.example.test/api/v1", evolus_sentiment_enabled=True,
                evolus_sentiment_tool="sentiment", evolus_tool_input_field="text", evolus_tool_extra=None,
                amd_base_url="https://amd.example.test/v1", amd_api_key="amd-key", amd_model="amd-model-x",
                amd_endpoint_kind="token_factory", image_provider="cloudflare")
    base.update(over)
    return SimpleNamespace(**base)


@pytest.fixture
def repo(tmp_path):
    db = Database(tmp_path / "t.db")
    db.migrate()
    return Repo(db)


def make_campaign(repo, n=2):
    cid = repo.create_campaign({
        "name": "T", "brand": "B", "product": "P", "description": "d", "objective": "Product launch", "tone": "Confident",
        "values": "", "language": "English", "approved_discount": None, "facts": [], "avoid": [],
        "audiences": [{"name": f"Aud{i}", "interests": "x", "needs": "y", "angle": "Performance", "context": "",
                       "source": "user"} for i in range(n)]})
    for i, a in enumerate(repo.list_audiences(cid)):
        eid = repo.ensure_experience_row(cid, a["id"])
        repo.save_experience_content(eid, {
            "headline": f"Headline {i}", "body_copy": f"Body copy number {i} for the test.", "cta": "Go",
            "creative_concept": "c" * 20, "personalization_rationale": "r" * 20, "visual_direction": "v" * 20,
            "hero_image_prompt": "h" * 25, "scenes": [], "start_scene": "s1"},
            {"provider": "amd", "model": "m", "fallback_used": False, "attempts": 1, "input_tokens": 1,
             "output_tokens": 1, "latency_ms": 1, "cost_usd": None})
    return cid


# ---------------------------------------------------------------- parsing
@pytest.mark.parametrize("data,label,score", [
    ({"sentiment": "positive", "score": 0.9}, "positive", 0.9),
    ({"result": {"label": "negative", "confidence": 0.7}}, "negative", 0.7),
    ({"data": {"sentiment": "neutral"}}, "neutral", None),
    ([{"label": "positive"}], "positive", None),
    ("Positive", "Positive", None),
    ({"unrelated": 1}, None, None),
    ({"score": True}, None, None),
])
def test_parse_tone(data, label, score):
    assert parse_tone(data) == (label, score)


# ---------------------------------------------------------------- the API call
def test_call_tool_sends_documented_headers_and_url(repo):
    fake = FakeClient()
    b = EvolusBlocks(make_settings(evolus_tool_extra={"language": "en"}), repo, client=fake)
    res = asyncio.run(b.call_tool("Hello there"))
    assert res["ok"] and res["label"] == "positive" and res["score"] == 0.91
    call = fake.calls[0]
    assert call["url"] == "https://evolus.example.test/api/v1/ai-tools/sentiment/run"
    assert call["headers"]["X-ApiKey"] == "k-test-evolus" and call["headers"]["X-Application-Id"] == "app-test"
    assert call["json"] == {"language": "en", "text": "Hello there"}


@pytest.mark.parametrize("status,code", [(401, "auth_failed"), (404, "not_found"), (422, "bad_request"),
                                         (429, "rate_limited"), (503, "provider_unavailable"), (402, "quota_exhausted")])
def test_call_tool_maps_errors_without_raising(repo, status, code):
    b = EvolusBlocks(make_settings(), repo, client=FakeClient(lambda *a: FakeResp(status, {"error": "x"}, "boom")))
    res = asyncio.run(b.call_tool("t"))
    assert res["ok"] is False and res["error_code"] == code
    assert "k-test-evolus" not in json.dumps(res)


def test_call_tool_transport_error_and_non_json(repo):
    def boom(*a):
        raise ConnectionError("down")
    assert asyncio.run(EvolusBlocks(make_settings(), repo, client=FakeClient(boom)).call_tool("t"))["error_code"] == "unreachable"
    res = asyncio.run(EvolusBlocks(make_settings(), repo, client=FakeClient(lambda *a: FakeResp(200, None))).call_tool("t"))
    assert res["error_code"] == "bad_response"


def test_missing_credentials_makes_no_call(repo):
    fake = FakeClient()
    b = EvolusBlocks(make_settings(evolus_api_key=None), repo, client=fake)
    assert asyncio.run(b.call_tool("t"))["error_code"] == "missing_credentials" and not fake.calls


# ---------------------------------------------------------------- campaign run + storage
def test_disabled_does_nothing(repo):
    fake = FakeClient()
    b = EvolusBlocks(make_settings(evolus_sentiment_enabled=False), repo, client=fake)
    cid = make_campaign(repo)
    assert asyncio.run(b.run_for_campaign(cid)) is None and b.summary(cid) is None and not fake.calls


def test_run_for_campaign_checks_headline_and_body_per_audience(repo):
    fake = FakeClient()
    b = EvolusBlocks(make_settings(), repo, client=fake)
    cid = make_campaign(repo, n=2)
    s = asyncio.run(b.run_for_campaign(cid))
    assert len(fake.calls) == 4 and s["counts"] == {"ok": 4, "failed": 0}
    assert {(r["audience"], r["field"]) for r in s["results"]} == {
        ("Aud0", "headline"), ("Aud0", "body_copy"), ("Aud1", "headline"), ("Aud1", "body_copy")}
    # running again replaces the old rows instead of piling up
    asyncio.run(b.run_for_campaign(cid))
    assert len(b.summary(cid)["results"]) == 4


def test_failures_are_stored_not_raised_and_do_not_touch_provider_calls(repo):
    b = EvolusBlocks(make_settings(), repo, client=FakeClient(lambda *a: FakeResp(404, {}, "nope")))
    cid = make_campaign(repo, n=1)
    before = repo.db.one("SELECT COUNT(*) n FROM provider_calls")["n"]
    s = asyncio.run(b.run_for_campaign(cid))
    assert s["counts"] == {"ok": 0, "failed": 2} and s["results"][0]["error_code"] == "not_found"
    assert repo.db.one("SELECT COUNT(*) n FROM provider_calls")["n"] == before  # cost figures are untouched


def test_deleting_a_campaign_removes_its_tone_rows(repo):
    b = EvolusBlocks(make_settings(), repo, client=FakeClient())
    cid = make_campaign(repo, n=1)
    asyncio.run(b.run_for_campaign(cid))
    repo.delete_campaign(cid)
    assert repo.db.one("SELECT COUNT(*) n FROM evolus_tone_checks")["n"] == 0


def test_evidence_marks_only_non_local_success_as_real(repo):
    b = EvolusBlocks(make_settings(evolus_rest_base_url="http://127.0.0.1:9/api/v1"), repo, client=FakeClient())
    asyncio.run(b.run_test())
    ev = b.evidence()
    assert ev["has_local_success"] and not ev["has_real_success"] and ev["calls_recorded"] == 1
    b2 = EvolusBlocks(make_settings(), repo, client=FakeClient())
    asyncio.run(b2.run_test())
    assert b2.evidence()["has_real_success"]


# ---------------------------------------------------------------- AMD proof
def _call(repo, provider, purpose, host, ok=True, fb=False, out=100):
    repo.add_call(purpose=purpose, provider=provider, model="m", endpoint_host=host, ok=ok, latency_ms=50,
                  input_tokens=10, output_tokens=out, fallback_used=fb)


def test_amd_proof_counts_only_real_amd_calls(repo):
    for p in ("strategy", "experience", "experience", "review"):
        _call(repo, "amd", p, "amd.example.test")
    _call(repo, "amd", "experience", "127.0.0.1:9999")          # local fake: not proof
    _call(repo, "gemma", "experience", "generativelanguage.googleapis.com", fb=True)
    _call(repo, "amd", "test", "amd.example.test")              # connection test: not workload
    p = amd_proof.summarize(repo, make_settings())
    assert p["amd"]["real_calls"] == 4 and p["amd"]["local_test_calls"] == 1 and p["amd"]["connection_tests"] == 1
    assert p["amd"]["by_purpose"] == {"strategy": 1, "experience": 2, "review": 1}
    assert p["fallback_calls"] == 1 and p["share"]["amd_calls_pct"] == 80
    assert p["newest_content_written_by"]["provider"] in ("amd", "gemma")
    claim = p["suggested_claim"]
    assert "amd-model-x" in claim and "amd.example.test" in claim and "Not on AMD" in claim
    assert "everything" not in claim.lower()
    assert any("Token Factory" in w for w in p["warnings"])


def test_amd_proof_makes_no_claim_without_real_calls(repo):
    _call(repo, "amd", "experience", "localhost:8000")
    p = amd_proof.summarize(repo, make_settings())
    assert p["amd"]["real_calls"] == 0 and "no AMD claim" in p["suggested_claim"]
    assert any("local test endpoint" in w for w in p["warnings"])
    assert "# AMD proof summary" in amd_proof.to_markdown(p)


def test_amd_proof_flags_gemma_as_newest_writer(repo):
    _call(repo, "amd", "experience", "amd.example.test")
    _call(repo, "gemma", "experience", "generativelanguage.googleapis.com", fb=True)
    p = amd_proof.summarize(repo, make_settings())
    assert any("not AMD" in w for w in p["warnings"])
    assert [i for i in p["proof_items"] if i["id"] == "demo_label"][0]["status"] == "missing"


# ---------------------------------------------------------------- through the real app (uses the shared mock server)
def test_api_switched_off_by_default(client):
    from conftest import campaign_payload
    cid = client.post("/api/campaigns", json=campaign_payload()).json()["id"]
    assert client.get(f"/api/campaigns/{cid}").json()["tone_check"] is None
    assert client.get(f"/api/campaigns/{cid}/tone-check").json()["enabled"] is False
    assert client.post(f"/api/campaigns/{cid}/tone-check").status_code == 409
    assert client.post("/api/integrations/evolus_tone/test").status_code == 409
    states = {i["id"]: i["state"] for i in client.get("/api/integrations").json()["integrations"]}
    assert states["evolus_tone"] == "disabled"
    assert client.get("/api/proof/amd").status_code == 200


def test_api_tone_check_runs_after_review_and_never_changes_status(tmp_path, mock_server):
    from starlette.testclient import TestClient
    from app.main import create_app
    from conftest import create_and_generate, make_settings as app_settings

    # status with the feature OFF
    (tmp_path / "off").mkdir(exist_ok=True)
    off_status = None
    s_off = app_settings(tmp_path / "off", mock_server)
    with TestClient(create_app(s_off)) as c:
        off_status = create_and_generate(c)["detail"]["status"]

    (tmp_path / "on").mkdir(exist_ok=True)
    s_on = app_settings(tmp_path / "on", mock_server, evolus_sentiment_enabled=True, evolus_api_key="k-test-evolus",
                        evolus_app_id="app-test", evolus_rest_base_url="https://evolus.example.test/api/v1")
    with TestClient(create_app(s_on)) as c:
        blocks = c.app.state.vyra.generator.evolus_blocks
        fake = FakeClient()
        blocks._client = fake
        out = create_and_generate(c)
        tc = out["detail"]["tone_check"]
        assert tc["enabled"] and tc["counts"]["ok"] == len(out["detail"]["experiences"]) * 2 and fake.calls
        assert out["detail"]["status"] == off_status          # informational only
        assert c.post(f"/api/campaigns/{out['id']}/tone-check").status_code == 200
        assert c.post("/api/integrations/evolus_tone/test").json()["ok"] is True
        blob = c.get("/api/integrations").text + c.get("/api/proof/amd").text
        assert "k-test-evolus" not in blob


def test_amd_proof_counts_gemma_as_amd_only_when_served_from_the_amd_host(repo):
    host = "amd.example.test"
    for p in ("strategy", "experience", "review"):
        _call(repo, "amd", p, host)
    _call(repo, "gemma", "translate", host)
    _call(repo, "gemma", "translate", host)
    s = make_settings(gemma_openai_base_url=f"https://{host}/v1", gemma_model="Gemma-4-31B-IT")
    p = amd_proof.summarize(repo, s)
    assert p["amd"]["gemma_on_amd"]["enabled"] and p["amd"]["gemma_on_amd"]["by_purpose"] == {"translate": 2}
    assert not any("Gemma" in x for x in p["not_on_amd"])
    assert "Gemma" in p["suggested_claim"] and "2 translation call(s)" in p["suggested_claim"]
    assert p["amd"]["real_calls"] == 3          # the main AMD workload count is unchanged


def test_amd_proof_keeps_gemma_off_amd_on_the_google_api_route(repo):
    host = "amd.example.test"
    _call(repo, "amd", "experience", host)
    _call(repo, "gemma", "translate", "generativelanguage.googleapis.com")
    p = amd_proof.summarize(repo, make_settings())
    assert not p["amd"]["gemma_on_amd"]["enabled"]
    assert any("Gemma" in x and "Google's servers" in x for x in p["not_on_amd"])
    # a different host for Gemma than the AMD host is never counted as AMD
    p2 = amd_proof.summarize(repo, make_settings(gemma_openai_base_url="https://other.example.test/v1"))
    assert not p2["amd"]["gemma_on_amd"]["enabled"]
