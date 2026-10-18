"""Interactive stories: choices must change what comes next."""
from conftest import create_and_generate
from app.services.story import check_story_graph


SID = "abcdefgh12"


def setup(client):
    d = create_and_generate(client)["detail"]
    return d, d["experiences"][0]


def test_choices_lead_to_different_scenes_and_endings(client):
    d, e = setup(client)
    url = f"/api/experiences/{e['id']}/play"
    start = client.post(url, json={"session_id": SID}).json()
    assert start["scene"]["id"] == "s1" and len(start["scene"]["choices"]) == 2
    assert "next" not in start["scene"]["choices"][0]          # the server, not the page, resolves choices
    a = client.post(url, json={"session_id": SID, "scene_id": "s1", "choice_id": "s1a"}).json()
    b = client.post(url, json={"session_id": SID, "scene_id": "s1", "choice_id": "s1b"}).json()
    assert a["scene"]["id"] == "s2" and b["scene"]["id"] == "s3"
    assert a["scene"]["narration"] != b["scene"]["narration"]
    end1 = client.post(url, json={"session_id": SID, "scene_id": "s2", "choice_id": "s2a"}).json()
    end2 = client.post(url, json={"session_id": SID, "scene_id": "s2", "choice_id": "s2b"}).json()
    assert end1["is_ending"] and end2["is_ending"] and end1["scene"]["id"] != end2["scene"]["id"]


def test_invalid_choices_rejected(client):
    d, e = setup(client)
    url = f"/api/experiences/{e['id']}/play"
    assert client.post(url, json={"session_id": SID, "scene_id": "s1", "choice_id": "zzz"}).status_code == 400
    assert client.post(url, json={"session_id": SID, "scene_id": "nope", "choice_id": "s1a"}).status_code == 400
    assert client.post(url, json={"session_id": SID, "scene_id": "s1"}).status_code == 400
    assert client.post(url, json={"session_id": "x"}).status_code == 422


def test_events_feed_real_analytics(client):
    assert client.get("/api/analytics/summary").json()["viewer"]["has_events"] is False
    d, e = setup(client)
    url = f"/api/experiences/{e['id']}/play"
    client.post(url, json={"session_id": SID})
    client.post(url, json={"session_id": SID, "scene_id": "s1", "choice_id": "s1a"})
    client.post(url, json={"session_id": SID, "scene_id": "s2", "choice_id": "s2a"})
    client.post("/api/events", json={"session_id": SID, "type": "cta_clicked", "experience_id": e["id"]})
    v = client.get("/api/analytics/summary").json()["viewer"]
    row = v["per_experience"][0]
    assert v["has_events"] and v["sessions"] == 1
    assert (row["opens"], row["choices"], row["completions"], row["cta_clicks"]) == (1, 2, 1, 1)
    assert row["completion_rate"] == 1.0


def test_graph_validator_catches_problems():
    def sc(i, nxt=None, end=False):
        return {"id": i, "is_ending": end, "choices": [] if end else [{"id": f"{i}{k}", "label": f"L{i}{k}", "next": n} for k, n in enumerate(nxt)]}
    ok = [sc("s1", ["s2", "s3"]), sc("s2", end=True), sc("s3", end=True)]
    assert check_story_graph(ok, "s1") == []
    assert check_story_graph(ok, "zz")                                           # unknown start
    assert check_story_graph([sc("s1", ["s2", "s2"]), sc("s2", end=True), sc("s3", end=True)], "s1")  # same target twice
    assert check_story_graph([sc("s1", ["s2", "s3"]), sc("s2", ["s1", "s3"]), sc("s3", end=True)], "s1")  # loop
    assert check_story_graph([sc("s1", ["s2", "s3"]), sc("s2", end=True), sc("s3", end=True), sc("s4", end=True)], "s1")  # unreachable
    assert check_story_graph([sc("s1", ["s2", "s3"]), sc("s2", end=True), sc("s3", ["s2", "s2"])], "s1")
