"""Brand-safety: deterministic rules (pure functions) and the AI-assisted layer (mocked model)."""
from conftest import create_and_generate, set_mock, campaign_payload
from app.config import Settings
from app.services import safety


def camp(**o):
    c = {"id": "c", "name": "Launch", "brand": "Northwind", "product": "Stride 4", "description": "A shoe.",
         "tone": "Confident", "facts": ["Cushioned midsole", "Recycled mesh upper"], "avoid": ["guaranteed"],
         "approved_discount": None}
    c.update(o)
    return c


def exp(aid, headline="Run your way", body="Built for people who run.", cta="Shop now", narr="You head out."):
    sc = [{"id": "s1", "title": "Start", "narration": narr, "choices": [{"id": "a", "label": "Go", "next": "s2"}, {"id": "b", "label": "Wait", "next": "s3"}]},
          {"id": "s2", "title": "A", "narration": "End A", "choices": [], "is_ending": True},
          {"id": "s3", "title": "B", "narration": "End B", "choices": [], "is_ending": True}]
    return {"audience_id": aid, "status": "ready", "headline": headline, "body_copy": body, "cta": cta, "scenes": sc}


AUDS = [{"id": "a1", "name": "Athlete", "interests": "running", "needs": "speed", "context": ""},
        {"id": "a2", "name": "Style", "interests": "fashion", "needs": "identity", "context": ""}]


def checks(findings):
    return {(f["check"], f["severity"]) for f in findings}


def test_prohibited_phrase_is_error():
    f = safety.deterministic_review(camp(), AUDS, [exp("a1", body="Guaranteed to feel great"), exp("a2", headline="Own it", body="Style first.")])
    assert ("prohibited_phrase", "error") in checks(f)


def test_unsupported_claim_vs_supported_claim():
    f = safety.deterministic_review(camp(), AUDS, [exp("a1", body="Eco-friendly and clinically proven."), exp("a2", headline="Own it", body="Style first.")])
    assert ("unsupported_claim", "error") in checks(f)
    f2 = safety.deterministic_review(camp(), AUDS, [exp("a1", body="Made with a recycled mesh upper."), exp("a2", headline="Own it", body="Style first.")])
    assert not [x for x in f2 if x["check"] == "unsupported_claim"]   # a verified fact supports it


def test_discount_rules():
    bad = safety.deterministic_review(camp(), AUDS, [exp("a1", body="Get 20% off today"), exp("a2", headline="Own it", body="Style.")])
    assert ("unapproved_offer", "error") in checks(bad)
    ok = safety.deterministic_review(camp(approved_discount="15% off your first order"), AUDS,
                                     [exp("a1", body="Enjoy 15% off your first order"), exp("a2", headline="Own it", body="Style.")])
    assert not [x for x in ok if x["check"] in ("unapproved_offer", "discount_mismatch")]
    mism = safety.deterministic_review(camp(approved_discount="15% off"), AUDS, [exp("a1", body="Enjoy 25% off"), exp("a2", headline="Own it", body="Style.")])
    assert ("discount_mismatch", "error") in checks(mism)
    price = safety.deterministic_review(camp(), AUDS, [exp("a1", body="Only $49 now"), exp("a2", headline="Own it", body="Style.")])
    assert ("unapproved_price", "error") in checks(price)


def test_personal_data_and_sensitive_inference():
    f = safety.deterministic_review(camp(), AUDS, [exp("a1", body="Write to jane@example.com or call +39 333 123 4567"), exp("a2", headline="Own it", body="Style.")])
    assert ("personal_data", "error") in checks(f)
    f = safety.deterministic_review(camp(), AUDS, [exp("a1", body="You are struggling with depression, so run."), exp("a2", headline="Own it", body="Style.")])
    assert ("sensitive_inference", "error") in checks(f)
    auds = [{**AUDS[0], "needs": "people with diabetes"}, AUDS[1]]
    f = safety.deterministic_review(camp(), auds, [exp("a1"), exp("a2", headline="Own it", body="Style.")])
    assert ("sensitive_audience", "warning") in checks(f)


def test_numbers_must_come_from_facts():
    f = safety.deterministic_review(camp(), AUDS, [exp("a1", body="Cuts fatigue by 40 percent."), exp("a2", headline="Own it", body="Style.")])
    assert ("unsupported_number", "warning") in checks(f)


def test_audience_differentiation():
    same = safety.deterministic_review(camp(), AUDS, [exp("a1"), exp("a2")])
    assert ("low_differentiation", "error") in checks(same)
    diff = safety.deterministic_review(camp(), AUDS, [exp("a1", headline="Chase every kilometre", body="Training blocks, tempo days and recovery runs, all cushioned.", narr="Dawn track session."),
                                                      exp("a2", headline="Wear the street", body="Colour, texture and attitude for the pavement catwalk.", narr="Gallery opening night.")])
    assert not [x for x in diff if x["check"] == "low_differentiation"]


def test_no_facts_is_reported_not_hidden():
    f = safety.deterministic_review(camp(facts=[]), AUDS, [exp("a1", headline="Chase every kilometre"), exp("a2", headline="Wear the street", body="Pavement catwalk colour.")])
    assert ("no_verified_facts", "info") in checks(f)


def test_status_rules():
    s = Settings()
    auds = AUDS
    ready = [exp("a1") | {"id": "e1"}, exp("a2") | {"id": "e2"}]
    assert safety.compute_status(auds, [], None, s)[0] == "draft"
    assert safety.compute_status(auds, ready[:1], None, s)[0] == "draft"
    assert safety.compute_status(auds, ready, None, s)[0] == "needs_review"
    rv = safety.build_review([{"id": "1", "check": "x", "title": "t", "severity": "error", "source": "deterministic", "audience": None, "field": None, "message": "m", "evidence": "", "suggestion": ""}], None)
    assert safety.compute_status(auds, ready, rv, s)[0] == "needs_review"
    ok = safety.build_review([], {"status": "ran", "findings": []})
    assert safety.compute_status(auds, ready, ok, s)[0] == "ready"
    no_ai = safety.build_review([], None)
    assert safety.compute_status(auds, ready, no_ai, s)[0] == "ready"
    assert safety.compute_status(auds, ready, no_ai, Settings(require_ai_review=True))[0] == "needs_review"
    stale = [{**ready[0], "status": "stale"}, ready[1]]
    assert "edited" in safety.compute_status(auds, stale, ok, s)[1]


def test_ai_review_layer_marks_and_verifies(client, mock_server):
    set_mock(mock_server, "amd", "review_finding", 1)
    out = create_and_generate(client)
    d = out["detail"]
    ai = [f for f in d["review"]["findings"] if f["source"] == "ai"]
    assert len(ai) == 2 and d["review"]["ai"]["status"] == "ran"
    quoted = [f for f in ai if f["check"] == "ai_voice"][0]
    bogus = [f for f in ai if f["check"] == "ai_unsupported_claim"][0]
    assert all(f.get("by") == d["review"]["ai"]["provider"] for f in ai)   # every AI finding names the real provider
    assert quoted["severity"] == "error"                       # quote found in the generated text: kept
    assert bogus["severity"] == "warning" and "unverified" in bogus["message"]   # quote not found: downgraded
    assert d["status"] == "needs_review"
    assert "probabilistic" in d["review"]["disclaimer"]


def test_ai_review_failure_is_reported_not_faked(client, mock_server):
    out = create_and_generate(client)
    set_mock(mock_server, "amd", "http500", -1)
    from conftest import wait_job
    r = client.post(f"/api/campaigns/{out['id']}/review", json={"ai": True})
    assert r.status_code == 202
    assert wait_job(client, r.json()["job"]["id"])["status"] == "succeeded"
    d = client.get(f"/api/campaigns/{out['id']}").json()
    assert d["review"]["ai"]["status"] == "failed" and d["review"]["ai"]["message"]
    assert not [f for f in d["review"]["findings"] if f["source"] == "ai"]
    assert d["review"]["deterministic"]["count"] >= 0


def test_human_edit_reruns_rules(client):
    out = create_and_generate(client)
    e = out["detail"]["experiences"][0]
    r = client.patch(f"/api/experiences/{e['id']}", json={"body_copy": "Guaranteed comfort, 40% off today."})
    assert r.status_code == 200 and r.json()["human_edited"] is True
    d = client.get(f"/api/campaigns/{out['id']}").json()
    kinds = {f["check"] for f in d["review"]["findings"]}
    assert {"prohibited_phrase", "unapproved_offer"} <= kinds and d["status"] == "needs_review"
    assert client.patch(f"/api/experiences/{e['id']}", json={}).status_code == 422
