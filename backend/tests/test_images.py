"""Image generation: real bytes stored, retrievable, deduplicated, failures reported. (Mocked image endpoint.)"""
import io
import struct

import pytest
from conftest import create_and_generate, wait_job, set_mock, make_settings
from starlette.testclient import TestClient
from app.main import create_app
import mock_providers as mp


@pytest.fixture
def gen(client):
    out = create_and_generate(client)
    return out["detail"]["experiences"][0], out["id"]


def start_images(client, eid, **body):
    r = client.post(f"/api/experiences/{eid}/images", json=body)
    assert r.status_code in (200, 202), r.text
    return r.json()


def test_hero_image_is_generated_stored_and_served(client, gen, settings):
    e, cid = gen
    r = start_images(client, e["id"], scope="hero")
    assert wait_job(client, r["job"]["id"])["status"] == "succeeded"
    d = client.get(f"/api/campaigns/{cid}").json()
    img = [i for i in d["experiences"][0]["images"] if i["scene_key"] == "hero"][0]
    assert img["status"] == "succeeded" and img["url"].startswith("/media/") and img["provider"] == "pollinations"
    assert img["width"] == 24 and img["bytes"] > 50 and img["aspect_ratio"] == "4:3"
    served = client.get(img["url"])
    assert served.status_code == 200 and served.headers["content-type"] == "image/png"
    assert served.content[:8] == b"\x89PNG\r\n\x1a\n" and len(served.content) == img["bytes"]
    assert (settings.media_dir / img["url"].split("/")[-1]).is_file()
    assert d["experiences"][0]["hero_url"] == img["url"]


def test_scene_images_all_scope_and_reuse(client, gen, mock_server):
    e, cid = gen
    r = start_images(client, e["id"], scope="all")
    assert wait_job(client, r["job"]["id"])["status"] == "succeeded"
    imgs = client.get(f"/api/experiences/{e['id']}").json()["images"]
    assert len([i for i in imgs if i["status"] == "succeeded"]) == 1 + len(e["scenes"])
    again = start_images(client, e["id"], scope="all")          # nothing to do: no duplicate spend
    assert again["job"] is None and "already exist" in again["message"]
    n_calls = len([c for c in __import__("httpx").get(f"{mock_server.base}/__calls", trust_env=False).json() if c.get("image")])
    assert n_calls == 1 + len(e["scenes"])


def test_duplicate_requests_do_not_double_generate(client, gen, mock_server):
    e, cid = gen
    set_mock(mock_server, "pollinations_image", "slow", 1)
    a = start_images(client, e["id"], scope="hero")
    b = start_images(client, e["id"], scope="hero")
    assert b["already_running"] is True and b["job"]["id"] == a["job"]["id"]
    wait_job(client, a["job"]["id"])
    calls = [c for c in __import__("httpx").get(f"{mock_server.base}/__calls", trust_env=False).json() if c.get("image")]
    assert len(calls) == 1


def test_blocked_prompt_fails_clearly_and_can_be_retried(client, gen, mock_server):
    e, cid = gen
    set_mock(mock_server, "pollinations_image", "blocked", 1)
    r = start_images(client, e["id"], scope="hero")
    job = wait_job(client, r["job"]["id"])
    assert job["status"] == "failed" and job["error"]["code"] == "blocked"
    img = client.get(f"/api/experiences/{e['id']}").json()["images"][0]
    assert img["status"] == "failed" and img["error_code"] == "blocked" and img["url"] is None
    retry = client.post(f"/api/images/{img['id']}/retry")
    assert retry.status_code == 202
    assert wait_job(client, retry.json()["job"]["id"])["status"] == "succeeded"
    img2 = client.get(f"/api/experiences/{e['id']}").json()["images"][0]
    assert img2["status"] == "succeeded" and img2["url"]
    assert client.post(f"/api/images/{img['id']}/retry").status_code == 409   # only failed images retry


@pytest.mark.parametrize("mode,code", [("no_image", "bad_response"), ("not_an_image", "bad_response"), ("http429", "rate_limited"), ("http401", "auth_failed")])
def test_image_failure_modes(client, gen, mock_server, mode, code):
    e, cid = gen
    set_mock(mock_server, "pollinations_image", mode, -1)
    r = start_images(client, e["id"], scope="hero")
    job = wait_job(client, r["job"]["id"])
    assert job["status"] == "failed" and job["error"]["code"] == code, job
    assert [f for f in (client.get(f"/api/experiences/{e['id']}").json()["images"]) if f["status"] == "succeeded"] == []


def test_not_configured_and_not_ready(tmp_path, mock_server):
    s = make_settings(tmp_path, mock_server, pollinations_api_key=None)
    with TestClient(create_app(s)) as c:
        out = create_and_generate(c)
        e = out["detail"]["experiences"][0]
        r = c.post(f"/api/experiences/{e['id']}/images", json={})
        assert r.status_code == 503 and r.json()["error"]["code"] == "image_provider_not_configured"


def test_daily_cap(tmp_path, mock_server):
    s = make_settings(tmp_path, mock_server, image_max_per_day=1)
    with TestClient(create_app(s)) as c:
        e = create_and_generate(c)["detail"]["experiences"][0]
        r = start_images(c, e["id"], scope="all")
        job = wait_job(c, r["job"]["id"])
        imgs = c.get(f"/api/experiences/{e['id']}").json()["images"]
        assert sum(i["status"] == "succeeded" for i in imgs) == 1
        assert any(i["error_code"] == "daily_cap" for i in imgs)


def test_regenerating_content_removes_stale_images(client, gen, settings):
    e, cid = gen
    wait_job(client, start_images(client, e["id"], scope="hero")["job"]["id"])
    files_before = list(settings.media_dir.glob("*.png"))
    assert len(files_before) == 1
    r = client.post(f"/api/experiences/{e['id']}/regenerate")
    assert wait_job(client, r.json()["job"]["id"])["status"] == "succeeded"
    assert list(settings.media_dir.glob("*.png")) == []
    assert client.get(f"/api/experiences/{e['id']}").json()["images"] == []


def test_delete_campaign_removes_media(client, gen, settings):
    e, cid = gen
    wait_job(client, start_images(client, e["id"], scope="hero")["job"]["id"])
    assert client.delete(f"/api/campaigns/{cid}").status_code == 200
    assert list(settings.media_dir.glob("*.png")) == []
    assert client.get(f"/api/campaigns/{cid}").status_code == 404


def test_product_reference_image_upload_rules(client, gen, mock_server):
    e, cid = gen
    url = f"/api/campaigns/{cid}/product-image"
    assert client.post(url, files={"file": ("a.png", b"<html>not an image</html>", "image/png")}).status_code == 415
    assert client.post(url, files={"file": ("a.png", b"x" * (5 * 1024 * 1024 + 10), "image/png")}).status_code == 413
    assert client.post(url, json={}).status_code == 400
    ok = client.post(url, files={"file": ("../../evil.png", mp.png("p"), "text/plain")})
    assert ok.status_code == 200 and ok.json()["product_image_url"].startswith("/media/")
    name = ok.json()["product_image_url"].split("/")[-1]
    assert ".." not in name and client.get(ok.json()["product_image_url"]).status_code == 200
    wait_job(client, start_images(client, e["id"], scope="hero")["job"]["id"])   # generation still works with a reference on disk
    assert client.delete(url).status_code == 200


def test_media_route_rejects_traversal(client):
    for bad in ["..%2F..%2Fetc%2Fpasswd", "x.png", "a" * 32 + ".exe", "A" * 32 + ".png"]:
        assert client.get(f"/media/{bad}").status_code == 404


def test_all_image_calls_go_to_image_provider_never_gemma(client, gen, mock_server):
    e, cid = gen
    ref = client.post(f"/api/campaigns/{cid}/product-image", files={"file": ("p.png", mp.png("p"), "image/png")})
    assert ref.status_code == 200
    assert wait_job(client, start_images(client, e["id"], scope="all", force=True)["job"]["id"])["status"] == "succeeded"
    calls = __import__("httpx").get(f"{mock_server.base}/__calls", trust_env=False).json()
    img_calls = [c for c in calls if c.get("image")]
    assert len(img_calls) == 1 + len(e["scenes"])
    assert all(c["provider"] == "pollinations" for c in img_calls)
    assert all(c["auth"] == f"Bearer {mp.POLLINATIONS_KEY}" and c["model"] == "mock-flux" for c in img_calls)
    assert not [c for c in calls if c["provider"] == "gemma" and c.get("image")]
    # the prompt must not claim an attached reference photo the image provider never receives
    assert all("attached reference" not in __import__("urllib.parse").parse.unquote(c["prompt"]) for c in img_calls)
    h = client.get("/api/health").json()
    assert h["image"]["provider"] == "pollinations" and h["image"]["configured"] is True
    ids = [i["id"] for i in client.get("/api/integrations").json()["integrations"]]
    assert "image_generation" in ids and "gemma_image" not in ids and "pollinations_image" not in ids


def test_pollinations_size_follows_aspect_ratio():
    from app.providers.pollinations import _size_for
    assert _size_for("4:3") == (1024, 768) and _size_for("1:1") == (1024, 1024)
    assert _size_for("9:16") == (576, 1024) and _size_for("16:9") == (1024, 576) and _size_for("junk") == (1024, 768)


# ---------------------------------------------------------------- Cloudflare main, Pollinations backup
def _cf_settings(tmp_path, mock_server, **over):
    return make_settings(tmp_path, mock_server, image_provider="cloudflare", **over)


def _image_calls(mock_server):
    return [c for c in __import__("httpx").get(f"{mock_server.base}/__calls", trust_env=False).json() if c.get("image")]


def test_cloudflare_working_means_pollinations_is_never_called(tmp_path, mock_server):
    with TestClient(create_app(_cf_settings(tmp_path, mock_server))) as c:
        e = create_and_generate(c)["detail"]["experiences"][0]
        assert wait_job(c, start_images(c, e["id"], scope="all")["job"]["id"])["status"] == "succeeded"
        calls = _image_calls(mock_server)
        assert len(calls) == 1 + len(e["scenes"]) and all(x["provider"] == "cloudflare" for x in calls)
        imgs = c.get(f"/api/experiences/{e['id']}").json()["images"]
        assert all(i["provider"] == "cloudflare" for i in imgs if i["status"] == "succeeded")
        h = c.get("/api/health").json()["image"]
        assert h["provider"] == "cloudflare" and h["backup"] == "pollinations"


@pytest.mark.parametrize("mode", ["http401", "http429", "http500", "no_image"])
def test_cloudflare_failure_falls_back_to_pollinations(tmp_path, mock_server, mode):
    with TestClient(create_app(_cf_settings(tmp_path, mock_server))) as c:
        e = create_and_generate(c)["detail"]["experiences"][0]
        set_mock(mock_server, "cloudflare_image", mode, -1)
        assert wait_job(c, start_images(c, e["id"], scope="hero")["job"]["id"])["status"] == "succeeded"
        img = [i for i in c.get(f"/api/experiences/{e['id']}").json()["images"] if i["status"] == "succeeded"][0]
        assert img["provider"] == "pollinations" and img["url"]
        assert c.get(img["url"]).status_code == 200
        assert any(x["provider"] == "pollinations" for x in _image_calls(mock_server))


def test_unconfigured_cloudflare_uses_pollinations(tmp_path, mock_server):
    with TestClient(create_app(_cf_settings(tmp_path, mock_server, cf_api_token=None))) as c:
        e = create_and_generate(c)["detail"]["experiences"][0]
        assert wait_job(c, start_images(c, e["id"], scope="hero")["job"]["id"])["status"] == "succeeded"
        calls = _image_calls(mock_server)
        assert calls and all(x["provider"] == "pollinations" for x in calls)


def test_both_providers_failing_reports_error(tmp_path, mock_server):
    with TestClient(create_app(_cf_settings(tmp_path, mock_server))) as c:
        e = create_and_generate(c)["detail"]["experiences"][0]
        set_mock(mock_server, "cloudflare_image", "http500", -1)
        set_mock(mock_server, "pollinations_image", "http500", -1)
        job = wait_job(c, start_images(c, e["id"], scope="hero")["job"]["id"])
        assert job["status"] == "failed"


def test_cloudflare_and_pollinations_both_unconfigured(tmp_path, mock_server):
    with TestClient(create_app(_cf_settings(tmp_path, mock_server, cf_api_token=None, pollinations_api_key=None))) as c:
        e = create_and_generate(c)["detail"]["experiences"][0]
        r = c.post(f"/api/experiences/{e['id']}/images", json={})
        assert r.status_code == 503 and r.json()["error"]["code"] == "image_provider_not_configured"
