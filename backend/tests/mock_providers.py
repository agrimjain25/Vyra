"""A local stand-in for the AI providers, used ONLY by automated tests and the offline demo mode.

It speaks the same wire formats as the real services (OpenAI-compatible chat, Gemma text via Google generateContent, and the Pollinations GET image endpoint) so the real adapters, retry logic, storage and UI can be exercised without keys.
Everything it returns is canned. Nothing produced here is real AI output, and the status page marks
loopback endpoints as "local test endpoint", never as a verified integration.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import socket
import struct
import threading
import time
import zlib

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

GOOGLE_KEY = "test-gemma-key"  # Google AI Studio style key (Gemma is served by the same API)
POLLINATIONS_KEY = "test-pollinations-key"
CF_TOKEN = "test-cf-token"
CF_ACCOUNT = "test-cf-account"
AMD_KEY = "test-amd-key"

STATE: dict = {"modes": {}, "calls": []}


def reset() -> None:
    STATE["modes"] = {}
    STATE["calls"] = []


def png(seed: str, size: int = 24) -> bytes:
    h = hashlib.sha256(seed.encode()).digest()
    r, g, b = h[0], h[1], h[2]
    raw = b"".join(b"\x00" + bytes([r, g, b]) * size for _ in range(size))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def _take_mode(provider: str) -> str:
    m = STATE["modes"].get(provider)
    if not m:
        return "ok"
    if m["count"] != -1:
        if m["count"] <= 0:
            STATE["modes"].pop(provider, None)
            return "ok"
        m["count"] -= 1
    return m["mode"]


# --------------------------------------------------------------------------- canned content

def _input(user: str) -> dict:
    i = user.find("INPUT_JSON:")
    if i < 0:
        return {}
    dec = json.JSONDecoder()
    try:
        return dec.raw_decode(user[i + len("INPUT_JSON:"):].lstrip())[0]
    except ValueError:
        return {}


def strategy(inp: dict) -> dict:
    brand = inp["campaign"]["brand"]
    return {
        "core_idea": f"{brand} meets people exactly where their own priorities are.",
        "brand_essence": f"{brand}: honest, direct, built around real use.",
        "voice_guidelines": "Short sentences. Concrete verbs. No hype.",
        "visual_identity": {"palette": "warm off-white with deep indigo accents", "lighting": "soft golden-hour light",
                            "composition": "close, human-scale framing with shallow depth of field",
                            "motifs": "motion blur, hands, everyday surfaces", "avoid_visuals": "text, logos, packaging"},
        "audiences": [{"audience_name": a["name"], "distinct_angle": f"Lead with {a['creative_angle'].lower()}",
                       "key_message": f"{a['name']} gets {a['needs_and_motivations']}",
                       "differentiation_notes": f"Different setting and verbs than the other audiences ({a['name']})."}
                      for a in inp["audiences"]],
    }


def experience(inp: dict, broken: str | None = None) -> dict:
    a = inp["this_audience"]
    name, angle, needs = a["name"], a["creative_angle"], a["needs_and_motivations"]
    brand, product = inp["campaign"]["brand"], inp["campaign"]["product"]
    facts = inp["campaign"]["verified_facts"]
    fact = facts[0] if facts else "everyday use"
    interests = a["interests"] or "daily life"
    out = {
        "headline": f"{angle} first, {name.replace('The ', '')}: {product}",
        "body_copy": (f"{brand} made {product} for {name}. You care about {needs}, so we start with {angle.lower()} "
                      f"and keep {interests} in view. Here is what is verified: {fact}. Try it in your own routine "
                      f"and decide for yourself what it does for {needs}."),
        "cta": f"Explore the {angle} story",
        "creative_concept": f"A short story that follows {name} through a day shaped by {interests}.",
        "personalization_rationale": f"{name} told us they value {needs}; the story leads with {angle.lower()} accordingly.",
        "visual_direction": f"Close, warm frames of {interests}; light and setting chosen for {name}.",
        "hero_image_prompt": f"A person enjoying {interests} wearing {product}, warm natural light, shallow depth of field, {angle} mood",
        "start_scene": "s1",
        "scenes": [
            {"id": "s1", "title": f"{name}: the start", "narration": f"Your morning begins with {interests}. {brand} {product} is already by the door, waiting for {needs}.",
             "image_prompt": f"Morning scene about {interests}, {product} by a door, soft light",
             "choices": [{"id": "s1a", "label": f"Head out for {interests}", "next": "s2"},
                         {"id": "s1b", "label": f"Take a slower look at the details", "next": "s3"}], "is_ending": False},
            {"id": "s2", "title": "Out in the world", "narration": f"You step out and the {angle.lower()} side of {product} shows up in how you move through {interests}.",
             "image_prompt": f"Outdoor scene, movement, {interests}, {product}",
             "choices": [{"id": "s2a", "label": "Push a little further", "next": "s4"},
                         {"id": "s2b", "label": "Stop and take it in", "next": "s5"}], "is_ending": False},
            {"id": "s3", "title": "A closer look", "narration": f"Up close, you read the specifics: {fact}. It fits what {name} wants from {needs}.",
             "image_prompt": f"Close up of {product} details, desk, warm light",
             "choices": [{"id": "s3a", "label": "Test it today", "next": "s4"},
                         {"id": "s3b", "label": "Save it for later", "next": "s5"}], "is_ending": False},
            {"id": "s4", "title": "Go further", "narration": f"By evening you have done more with {interests} than planned. {brand} invites you to explore the {angle} story.",
             "image_prompt": f"Evening scene, accomplishment, {interests}", "choices": [], "is_ending": True},
            {"id": "s5", "title": "Take your time", "narration": f"You pause and keep the moment. When you are ready, {brand} will be here: explore the {angle} story.",
             "image_prompt": f"Calm scene, reflection, {interests}", "choices": [], "is_ending": True},
        ],
    }
    if broken == "invalid_graph":
        out["scenes"][3]["choices"] = [{"id": "x1", "label": "Back", "next": "s1"}, {"id": "x2", "label": "On", "next": "s5"}]
        out["scenes"][3]["is_ending"] = False
    return out


def translation(inp: dict, mode: str | None = None) -> dict:
    src = inp["text_to_translate"]
    tag = "[" + inp["target_language"][:2].upper() + "] "
    scenes = [{"id": s["id"], "title": tag + s["title"], "narration": tag + s["narration"],
               "choices": [{"id": c["id"], "label": tag + c["label"]} for c in s["choices"]]} for s in src["scenes"]]
    if mode == "bad_ids":
        scenes[0]["id"] = "zz"
    return {"headline": tag + src["headline"], "body_copy": tag + src["body_copy"], "cta": tag + src["cta"],
            "scenes": scenes}


def review(inp: dict, mode: str) -> dict:
    exps = inp["experiences"]
    if mode == "review_finding" and exps:
        e = exps[0]
        quote = e["headline"][:18]
        return {"findings": [
            {"audience": e["audience"], "field": "headline", "category": "voice", "severity": "error",
             "message": "Headline tone feels off for the brand.", "evidence": quote, "suggestion": "Soften it."},
            {"audience": e["audience"], "field": "body_copy", "category": "unsupported_claim", "severity": "error",
             "message": "Claims something unverified.", "evidence": "this quote does not exist anywhere", "suggestion": "Remove."}],
            "summary": "Two findings."}
    return {"findings": [], "summary": "No problems found."}


def _content_for(provider: str, system: str, user: str) -> tuple[str, int, int]:
    task = re.search(r"TASK:\s*(\w+)", user)
    task = task.group(1) if task else "ping"
    peek = (STATE["modes"].get(provider) or {}).get("mode")
    if ((peek == "invalid_graph" and task != "experience") or (peek == "review_finding" and task != "review")
            or (peek == "bad_ids" and task != "translate")):
        mode = "ok"  # this failure mode targets a different task; leave it queued
    else:
        mode = _take_mode(provider)
    inp = _input(user)
    if task == "strategy":
        data = strategy(inp)
    elif task == "experience":
        data = experience(inp, "invalid_graph" if mode == "invalid_graph" else None)
    elif task == "review":
        data = review(inp, mode)
    elif task == "translate":
        data = translation(inp, mode)
    else:
        data = {"ok": True}
    text = json.dumps(data)
    if mode == "malformed":
        text = "Sure! Here is your campaign: {not json"
    if mode == "fenced":
        text = "<think>planning...</think>\n```json\n" + text + "\n```"
    return text, max(20, len(user) // 4), max(10, len(text) // 4)


def _fail(provider: str):
    """Returns an error response for failure modes (consumed before content is built)."""
    m = STATE["modes"].get(provider)
    if not m:
        return None
    mode = m["mode"]
    if mode in ("http500", "http401", "http429", "quota", "http404"):
        if _take_mode(provider) != mode:
            return None
        status, msg = {"http500": (500, "boom"), "http401": (401, "Incorrect API key provided"),
                       "http429": (429, "Rate limit reached, slow down"), "quota": (429, "You exceeded your current quota, please check your plan and billing"),
                       "http404": (404, "model not found")}[mode]
        headers = {"retry-after": "0"} if status == 429 else {}
        return JSONResponse({"error": {"message": msg}}, status_code=status, headers=headers)
    return None


async def _maybe_slow(provider: str) -> None:
    m = STATE["modes"].get(provider)
    if m and m["mode"] == "slow":
        _take_mode(provider)
        await asyncio.sleep(1.5)


# --------------------------------------------------------------------------- routes

async def openai_chat(request: Request):
    provider = request.path_params["prov"]
    body = await request.json()
    STATE["calls"].append({"provider": provider, "path": request.url.path, "auth": request.headers.get("authorization"),
                           "has_response_format": "response_format" in body})
    if request.headers.get("authorization") != f"Bearer {AMD_KEY}":
        return JSONResponse({"error": {"message": "Incorrect API key provided"}}, status_code=401)
    err = _fail(provider)
    if err:
        return err
    await _maybe_slow(provider)
    msgs = body["messages"]
    text, i, o = _content_for(provider, msgs[0]["content"], msgs[-1]["content"])
    return JSONResponse({"id": f"mock-{len(STATE['calls'])}", "model": body["model"], "object": "chat.completion",
                         "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
                         "usage": {"prompt_tokens": i, "completion_tokens": o, "total_tokens": i + o}})


def _gemma_common(request: Request, body: dict, is_image: bool):
    # Gemma is text-only in VYRA. An image request reaching it is a bug, so record it loudly.
    sys_in = "systemInstruction" in body
    json_in = "responseMimeType" in (body.get("generationConfig") or {})
    STATE["calls"].append({"provider": "gemma", "path": request.url.path, "image": is_image,
                           "key_header": request.headers.get("x-goog-api-key"), "system": sys_in, "json": json_in})
    if request.headers.get("x-goog-api-key") != GOOGLE_KEY:
        return JSONResponse({"error": {"message": "API key not valid. Please pass a valid API key."}}, status_code=400)
    mode = (STATE["modes"].get("gemma") or {}).get("mode")
    if mode == "reject_system" and sys_in:
        return JSONResponse({"error": {"message": "Developer instruction is not enabled for models/mock-gemma"}}, status_code=400)
    if mode == "reject_json" and json_in:
        return JSONResponse({"error": {"message": "JSON mode is not enabled for models/mock-gemma"}}, status_code=400)
    return _fail("gemma")


async def google_generate(request: Request):
    body = await request.json()
    gc = body.get("generationConfig", {})
    is_image = "IMAGE" in gc.get("responseModalities", [])
    parts = body["contents"][0]["parts"]
    user_text = " ".join(p.get("text", "") for p in parts if "text" in p)
    err = _gemma_common(request, body, is_image)
    if err:
        return err
    await _maybe_slow("gemma")
    mode = (STATE["modes"].get("gemma") or {}).get("mode")
    if mode in ("reject_system", "reject_json"):  # persistent "API limitation" modes: do not consume them
        STATE["modes"]["gemma"]["count"] = -1
        saved = STATE["modes"].pop("gemma")
        text, i, o = _content_for("gemma", "", user_text)
        STATE["modes"]["gemma"] = saved
    else:
        text, i, o = _content_for("gemma", "", user_text)
    return JSONResponse({"candidates": [{"content": {"parts": [{"thought": True, "text": "thinking, ignore me"}, {"text": text}]},
                                         "finishReason": "STOP"}],
                         "usageMetadata": {"promptTokenCount": i, "candidatesTokenCount": o}, "modelVersion": "mock-gemma-text"})


async def pollinations_image(request: Request):
    prompt = request.path_params["prompt"]
    q = request.query_params
    STATE["calls"].append({"provider": "pollinations", "path": request.url.path, "image": True,
                           "auth": request.headers.get("authorization"), "model": q.get("model"),
                           "width": q.get("width"), "height": q.get("height"), "prompt": prompt})
    if request.headers.get("authorization") != f"Bearer {POLLINATIONS_KEY}":
        return JSONResponse({"error": {"message": "Invalid API key"}}, status_code=401)
    err = _fail("pollinations_image")
    if err:
        return err
    mode = _take_mode("pollinations_image")
    await _maybe_slow("pollinations_image")
    if mode == "blocked":
        return JSONResponse({"error": {"message": "Prompt blocked by safety policy"}}, status_code=400)
    if mode == "no_image":
        return Response(b'{"note": "no image today"}', media_type="application/json")
    if mode == "not_an_image":
        return Response(b"<html>nope</html>", media_type="image/png")
    assert q.get("width") and q.get("height"), "size missing"
    return Response(png(prompt + str(len(STATE["calls"]))), media_type="image/png")


async def cf_image(request: Request):
    """Mock Cloudflare Workers AI: POST /cloudflare/accounts/{acct}/ai/run/{model} -> {"result": {"image": b64}}."""
    body = await request.json()
    STATE["calls"].append({"provider": "cloudflare", "path": request.url.path, "image": True,
                           "auth": request.headers.get("authorization"), "model": request.path_params["model"],
                           "prompt": body.get("prompt", "")})
    if request.headers.get("authorization") != f"Bearer {CF_TOKEN}":
        return JSONResponse({"success": False, "errors": [{"message": "Authentication error"}]}, status_code=401)
    err = _fail("cloudflare_image")
    if err:
        return err
    mode = _take_mode("cloudflare_image")
    await _maybe_slow("cloudflare_image")
    if mode == "no_image":
        return JSONResponse({"result": {}, "success": True})
    import base64
    img = png("cf" + body.get("prompt", "") + str(len(STATE["calls"])))
    return JSONResponse({"result": {"image": base64.b64encode(img).decode()}, "success": True})


async def control(request: Request):
    body = await request.json()
    if body.get("reset"):
        reset()
    if body.get("provider"):
        STATE["modes"][body["provider"]] = {"mode": body["mode"], "count": body.get("count", 1)}
    return JSONResponse({"ok": True, "modes": STATE["modes"]})


async def calls(request: Request):
    return JSONResponse(STATE["calls"])


def build_app() -> Starlette:
    return Starlette(routes=[
        Route("/{prov}/v1/chat/completions", openai_chat, methods=["POST"]),
        Route("/google/v1beta/models/{model}:generateContent", google_generate, methods=["POST"]),
        Route("/pollinations/image/{prompt:path}", pollinations_image),
        Route("/cloudflare/accounts/{acct}/ai/run/{model:path}", cf_image, methods=["POST"]),
        Route("/__control", control, methods=["POST"]),
        Route("/__calls", calls),
    ])


class MockServer:
    def __init__(self, port: int | None = None):
        if port is None:
            with socket.socket() as s:
                s.bind(("127.0.0.1", 0))
                port = s.getsockname()[1]
        self.port = port
        self.server = uvicorn.Server(uvicorn.Config(build_app(), host="127.0.0.1", port=port, log_level="error"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def start(self) -> "MockServer":
        self.thread.start()
        for _ in range(100):
            if self.server.started:
                return self
            time.sleep(0.05)
        raise RuntimeError("mock provider server did not start")

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=5)

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"
