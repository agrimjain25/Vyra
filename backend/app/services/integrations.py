"""Truthful integration status.

A state of "verified_working" is only reported when this server has actually recorded a successful call
to that provider on a non-local endpoint. Configuration alone is never presented as "connected".
"""
from __future__ import annotations

from ..config import Settings
from ..providers.base import is_loopback
from ..providers.hub import ProviderHub
from ..repo import Repo
from .evolus_blocks import EvolusBlocks

STATES = {
    "verified_working": "Verified working: a real call succeeded",
    "configured": "Configured, no successful call recorded yet",
    "local_test_endpoint": "Calls succeed, but only against a local test endpoint",
    "error": "Last call failed",
    "needs_credentials": "Needs credentials",
    "not_implemented": "Not implemented",
    "documentation_only": "Documentation only",
    "disabled": "Switched off (optional feature, enabled with an environment variable)",
}


def _evidence(repo: Repo, provider: str, purposes: tuple[str, ...]) -> dict:
    marks = ",".join("?" * len(purposes))
    ok = repo.db.query(
        f"""SELECT ts, model, endpoint_host, latency_ms, input_tokens, output_tokens, request_id FROM provider_calls
            WHERE provider=? AND ok=1 AND error_code IS NULL AND purpose IN ({marks}) ORDER BY id DESC LIMIT 50""",
        (provider, *purposes))
    bad = repo.db.one(f"""SELECT ts, error_code, error_message, endpoint_host FROM provider_calls
                          WHERE provider=? AND ok=0 AND purpose IN ({marks}) ORDER BY id DESC LIMIT 1""",
                      (provider, *purposes))
    real = [r for r in ok if not is_loopback(r["endpoint_host"] or "")]
    local = [r for r in ok if is_loopback(r["endpoint_host"] or "")]
    total = repo.db.one(f"SELECT COUNT(*) n FROM provider_calls WHERE provider=? AND purpose IN ({marks})",
                        (provider, *purposes))["n"]
    last_ok = (real or local or [None])[0]
    return {"calls_recorded": total, "last_success": last_ok, "has_real_success": bool(real),
            "has_local_success": bool(local), "last_error": bad,
            "error_is_latest": bool(bad and (not last_ok or bad["ts"] > last_ok["ts"]))}


def _state(configured: bool, ev: dict) -> str:
    if not configured:
        return "needs_credentials"
    if ev["has_real_success"] and not ev["error_is_latest"]:
        return "verified_working"
    if ev["error_is_latest"]:
        return "error"
    if ev["has_local_success"] and not ev["has_real_success"]:
        return "local_test_endpoint"
    return "configured"


def build(settings: Settings, repo: Repo, hub: ProviderHub) -> list[dict]:
    s = settings
    chain = hub.chain_names("generation")
    review_chain = hub.chain_names("review")
    out: list[dict] = []

    # ---- AMD
    amd = hub.llms["amd"]
    ok, why = amd.configured()
    ev = _evidence(repo, "amd", ("strategy", "experience", "review", "test"))
    out.append({
        "id": "amd", "name": "AMD (hosted open model)", "kind": "required_hackathon_technology",
        "role": "Runs the campaign strategy, the audience experiences and the AI-assisted review (the main LLM workload).",
        "module": "backend/app/providers/openai_compat.py (OpenAI-compatible client) + providers/hub.py",
        "state": _state(ok, ev), "configured": ok, "problem": why,
        "in_use_as": "primary" if "amd" in chain[:1] else ("fallback" if "amd" in chain else "not selected"),
        "details": {"endpoint_host": amd.endpoint_host or None, "model": amd.model or None,
                    "declared_endpoint_kind": s.amd_endpoint_kind,
                    "json_mode": s.amd_json_mode, "price_configured": amd.price_in_per_mtok is not None},
        "evidence": ev,
        "caveat": ("This server can record which endpoint answered, with which model, how fast, and how many tokens. "
                   "It cannot itself prove the endpoint runs on AMD hardware: that rests on the endpoint being the "
                   "AMD-provided Token Factory or your own vLLM server on AMD Developer Cloud, as declared in "
                   "AMD_ENDPOINT_KIND. Confirm this against the official hackathon rules."),
    })
    # ---- Gemma
    g = hub.llms["gemma"]
    g_ok, g_why = g.configured()
    ev_g = _evidence(repo, "gemma", ("translate", "strategy", "experience", "review", "test"))
    tr_chain = hub.chain_names("translate")
    out.append({
        "id": "gemma", "name": "Gemma (Google open model)", "kind": "optional_partner_technology",
        "role": "Main multilingual translator (an Evolus agent translates if Gemma fails). Also the fallback for the brand "
                "review when Evolus fails, and for text generation when the AMD model fails. Gemma is not used for images.",
        "module": "backend/app/providers/gemma.py (GemmaTextProvider) + services/translate.py",
        "state": _state(g_ok, ev_g), "configured": g_ok, "problem": g_why,
        "in_use_as": "translation (main)" if tr_chain[:1] == ["gemma"] else ("fallback" if "gemma" in chain else "not selected"),
        "details": {"model": g.model or None, "endpoint_host": g.endpoint_host or None,
                    "route": (("AMD endpoint (OpenAI-compatible)" if g.endpoint_host == hub.llms["amd"].endpoint_host
                               else "self-hosted (OpenAI-compatible)") if s.gemma_openai_base_url else "Google AI Studio API")},
        "evidence": ev_g,
        "caveat": "Gemma is Google DeepMind's open-weights model family, a different model from Gemini. "
                  "Confirm the model id and the request format with the live test button. "
                  + ("This route calls the same AMD endpoint host as the main model. AMD's own dashboard is the proof of where it runs."
                     if s.gemma_openai_base_url and g.endpoint_host == hub.llms["amd"].endpoint_host else
                     "On the Google API route the model runs on Google's servers, not on AMD hardware."),
    })
    # ---- Image generation (Cloudflare main; Pollinations automatic backup)
    p_ok, p_why = hub.image.configured()
    ev_i = _evidence(repo, hub.image.name, ("image",))
    if hub.image.name == "pollinations":
        img_model, img_mod = s.pollinations_model, "providers/pollinations.py (PollinationsImageProvider)"
    elif hub.image.name == "cloudflare":
        img_model, img_mod = s.cf_image_model, "providers/cloudflare.py (CloudflareImageProvider)"
    else:
        img_model, img_mod = s.cf_image_model, "providers/cloudflare.py (CloudflareImageProvider)"
    out.append({
        "id": "image_generation", "name": f"{hub.image.label} (image generation)", "kind": "optional_partner_technology",
        "role": "Generates every campaign image: audience hero images and story scene images."
                + (f" If it fails, {hub.image_backup.label} is used automatically as a backup (only then)."
                   if hub.image_backup else ""),
        "module": f"backend/app/{img_mod} + services/images.py",
        "state": _state(p_ok, ev_i), "configured": p_ok, "problem": p_why, "in_use_as": "image generation",
        "details": {"provider": hub.image.name, "model": img_model, "endpoint_host": hub.image.endpoint_host or None,
                    "daily_cap": s.image_max_per_day,
                    "backup": hub.image_backup.name if hub.image_backup else None,
                    "backup_configured": bool(hub.image_backup and hub.image_backup.configured()[0])},
        "evidence": ev_i,
        "caveat": f"Models and limits depend on your {hub.image.label} account; confirm with the live test button. "
                  f"A product reference photo is not sent to {hub.image.label}.",
    })
    # ---- Evolus
    e_ok, e_why = hub.llms["evolus"].configured()
    ev_e = _evidence(repo, "evolus", ("review", "test", "strategy", "experience", "translate"))
    out.append({
        "id": "evolus", "name": "Evolus agent", "kind": "optional_partner_technology",
        "role": "Optional: an Evolus agent (OpenAI-compatible chat endpoint) can perform the AI-assisted brand review. "
                "Set REVIEW_LLM_PROVIDER=evolus. It is also the fallback translator if Gemma fails.",
        "module": "backend/app/providers/hub.py (evolus entry, OpenAI-compatible client)",
        "state": _state(e_ok, ev_e), "configured": e_ok, "problem": e_why,
        "in_use_as": "review" if review_chain[:1] == ["evolus"] else "not selected",
        "details": {"endpoint_host": hub.llms["evolus"].endpoint_host, "model": hub.llms["evolus"].model or None},
        "evidence": ev_e,
        "caveat": ("The agent is called through the Evolus OpenAI-compatible chat endpoint. A knowledge library is attached to "
                   "the agent inside your Evolus workspace (see evolus/README.md), not from this app. The Sentiment AI Tool is a "
                   "separate optional block (next card). Workflows, document extractors, sub-agents, MCP connectors and the chat "
                   "widget are NOT implemented. The Evolus track also requires the agent's model to run on AMD, which this "
                   "app cannot set up or check for you."),
    })
    # ---- Evolus AI Tool (second building block, optional)
    blocks = EvolusBlocks(s, repo)
    t_ok, t_why = blocks.configured()
    ev_t = blocks.evidence()
    if not blocks.enabled:
        t_state = "disabled"
    else:
        t_state = _state(t_ok, ev_t)
    out.append({
        "id": "evolus_tone", "name": "Evolus AI Tool (Sentiment)", "kind": "optional_partner_technology",
        "role": "Optional second Evolus building block: runs the Evolus Sentiment AI Tool on every audience's headline and message "
                "through the Evolus REST API (X-ApiKey + X-Application-Id). Informational only. Enable with EVOLUS_SENTIMENT=true.",
        "module": "backend/app/services/evolus_blocks.py",
        "state": t_state, "configured": t_ok and blocks.enabled, "problem": t_why if blocks.enabled else None,
        "in_use_as": "tone check" if blocks.enabled else "not selected",
        "details": {"endpoint_host": blocks.endpoint_host, "model": blocks.tool}, "evidence": ev_t,
        "caveat": ("The exact request body and reply of the Evolus Sentiment tool were not available when this was written. "
                   "If the test fails with a 400/404, check the interactive API reference in your Evolus workspace and adjust "
                   "EVOLUS_SENTIMENT_TOOL, EVOLUS_TOOL_INPUT_FIELD or EVOLUS_TOOL_EXTRA_JSON."),
    })
    # ---- PwC Italy
    out.append({
        "id": "pwc_italy", "name": "PwC Italy", "kind": "sponsor", "role": "No technical interface is described in the "
        "event materials, so nothing is integrated.", "module": None, "state": "not_implemented", "configured": False,
        "problem": None, "in_use_as": "n/a", "details": {}, "evidence": {},
        "caveat": "This app does not claim any PwC integration, endorsement or methodology.",
    })
    # ---- Vibe Generation
    out.append({
        "id": "vibe_generation", "name": "Vibe Generation (track)", "kind": "track",
        "role": "Challenge track. VYRA turns one brief into different, interactive audience experiences with a "
                "human-in-the-loop review. See docs/ELIGIBILITY_CHECKLIST.md for the criteria mapping.",
        "module": "whole application", "state": "documentation_only", "configured": True, "problem": None,
        "in_use_as": "track", "details": {}, "evidence": {},
        "caveat": "No official partnership or judging outcome is implied.",
    })
    return out
