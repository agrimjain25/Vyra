"""AMD proof summary: what really ran on the AMD endpoint, computed only from calls this server recorded.

Judges cannot see your .env file, so this turns the recorded calls into something you can show and screenshot:
the endpoint host, the model, how many successful calls and tokens, which parts of the workload they served, and
how much was served by a fallback (Gemma) instead. It also writes an exact sentence you can reuse in the README
and slides, and it lists the five proof items with what can and cannot be checked automatically.

It cannot prove the endpoint runs on AMD hardware. That proof is AMD's own Token Factory dashboard (or your own
vLLM server on AMD Developer Cloud). This module only reports what this app observed.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

WORKLOAD = ("strategy", "experience", "review")
PURPOSE_TEXT = {"strategy": "the campaign strategy", "experience": "the per-audience content and story generation",
                "review": "the AI-assisted brand review"}
EVIDENCE_JSON = Path(__file__).resolve().parents[3] / "evidence" / "amd_evidence.json"


def _is_loopback(host: str) -> bool:
    h = (host or "").split(":")[0].lower()
    return h in ("localhost", "127.0.0.1", "::1", "[::1]", "0.0.0.0") or h.startswith("127.")


def _join(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _evidence_file() -> dict:
    if not EVIDENCE_JSON.is_file():
        return {"exists": False}
    try:
        d = json.loads(EVIDENCE_JSON.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"exists": True, "readable": False}
    return {"exists": True, "readable": True, "collected_at": d.get("collected_at"), "successes": d.get("successes"),
            "failures": d.get("failures"), "median_latency_ms": d.get("median_latency_ms"),
            "endpoint_host": d.get("endpoint_host"), "endpoint_is_local": d.get("endpoint_is_local")}


def summarize(repo: Any, settings: Any) -> dict:
    s = settings
    amd_host = urlparse(s.amd_base_url or "").netloc or ""
    rows = repo.db.query(
        """SELECT provider, endpoint_host, purpose, COUNT(*) n, SUM(COALESCE(input_tokens,0)) i,
                  SUM(COALESCE(output_tokens,0)) o, SUM(fallback_used) fb, MIN(ts) first_ts, MAX(ts) last_ts
           FROM provider_calls WHERE ok=1 AND error_code IS NULL GROUP BY provider, endpoint_host, purpose""")

    amd_real = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "by_purpose": {}, "first": None, "last": None}
    amd_local_calls = 0
    amd_tests = 0
    others: dict[str, dict] = {}
    fallback_calls = 0
    for r in rows:
        if r["purpose"] == "test":
            if r["provider"] == "amd" and not _is_loopback(r["endpoint_host"] or ""):
                amd_tests += r["n"]
            continue
        if r["purpose"] not in WORKLOAD:
            continue
        fallback_calls += int(r["fb"] or 0)
        if r["provider"] == "amd":
            if _is_loopback(r["endpoint_host"] or ""):
                amd_local_calls += r["n"]
                continue
            amd_real["calls"] += r["n"]
            amd_real["input_tokens"] += int(r["i"] or 0)
            amd_real["output_tokens"] += int(r["o"] or 0)
            amd_real["by_purpose"][r["purpose"]] = amd_real["by_purpose"].get(r["purpose"], 0) + r["n"]
            amd_real["first"] = min(filter(None, [amd_real["first"], r["first_ts"]]))
            amd_real["last"] = max(filter(None, [amd_real["last"], r["last_ts"]]))
        else:
            o = others.setdefault(r["provider"], {"provider": r["provider"], "calls": 0, "output_tokens": 0, "by_purpose": {}})
            o["calls"] += r["n"]
            o["output_tokens"] += int(r["o"] or 0)
            o["by_purpose"][r["purpose"]] = o["by_purpose"].get(r["purpose"], 0) + r["n"]

    other_calls = sum(o["calls"] for o in others.values())
    other_out = sum(o["output_tokens"] for o in others.values())
    total_calls = amd_real["calls"] + other_calls
    total_out = amd_real["output_tokens"] + other_out
    share = {"amd_calls_pct": round(100 * amd_real["calls"] / total_calls) if total_calls else None,
             "amd_output_tokens_pct": round(100 * amd_real["output_tokens"] / total_out) if total_out else None,
             "scope": "text-generation calls recorded by this server (strategy, experiences, review); images excluded"}

    # which provider wrote the newest content (this is what the Home page label shows)
    last_exp = repo.db.one("""SELECT provider, model, endpoint_host, ts FROM provider_calls
                              WHERE purpose='experience' AND ok=1 AND error_code IS NULL ORDER BY id DESC LIMIT 1""")

    ev_file = _evidence_file()
    served_by_amd = [PURPOSE_TEXT[p] for p in WORKLOAD if amd_real["by_purpose"].get(p)]
    img = getattr(s, "image_provider", "cloudflare")
    not_on_amd = [f"image generation ({'Cloudflare Workers AI' if img == 'cloudflare' else 'Pollinations'}"
                  f"{', Pollinations as backup' if img == 'cloudflare' else ''})"]
    g = others.get("gemma")
    # Gemma counts as AMD-served only when it is called through the SAME AMD endpoint host as the main model
    # (GEMMA_OPENAI_BASE_URL equals the AMD host, for example Token Factory). On the Google API route it never does.
    gemma_host = urlparse(getattr(s, "gemma_openai_base_url", None) or "").netloc or ""
    gemma_on_amd = bool(gemma_host and amd_host and gemma_host == amd_host and not _is_loopback(gemma_host))
    gemma_amd_calls: dict[str, int] = {}
    gemma_amd_model = None
    if gemma_on_amd:
        for r in repo.db.query(
                """SELECT purpose, model, COUNT(*) n FROM provider_calls WHERE provider='gemma' AND endpoint_host=?
                   AND ok=1 AND error_code IS NULL AND purpose IN ('translate','strategy','experience','review')
                   GROUP BY purpose, model""", (gemma_host,)):
            gemma_amd_calls[r["purpose"]] = gemma_amd_calls.get(r["purpose"], 0) + r["n"]
            gemma_amd_model = r["model"] or gemma_amd_model
    if gemma_on_amd:
        pass  # Gemma is on AMD: it is described in the "On AMD" part of the claim, not in the "Not on AMD" list
    else:
        n_tr = (repo.db.one("SELECT COUNT(*) n FROM provider_calls WHERE provider='gemma' AND purpose='translate' "
                            "AND ok=1 AND error_code IS NULL") or {}).get("n", 0)
        not_on_amd.append("Gemma (multilingual translation, plus fallback for text and review"
                          + (f"; it answered {g['calls']} text call(s)" if g else "; it answered no recorded text calls")
                          + (f" and {n_tr} translation call(s)" if n_tr else "")
                          + ("; on the Google API route it runs on Google's servers" if not getattr(s, "gemma_openai_base_url", None) else "") + ")")
    e = others.get("evolus")
    not_on_amd.append("the model inside the Evolus agent"
                      + (f" (it answered {e['calls']} review call(s); it runs in your Evolus workspace, not through this app's AMD settings)"
                         if e else " (unless you connected it to your AMD model in the Evolus workspace)"))

    kind = getattr(s, "amd_endpoint_kind", "token_factory")
    warnings: list[str] = []
    if not amd_host:
        warnings.append("AMD_BASE_URL is not set.")
    if amd_local_calls and not amd_real["calls"]:
        warnings.append("Only calls to a local test endpoint were recorded. That is NOT AMD proof.")
    if fallback_calls:
        warnings.append(f"{fallback_calls} text call(s) were answered by a fallback provider. Check the Home page label "
                        "before recording the demo: it must not say Gemma.")
    if last_exp and last_exp["provider"] != "amd":
        warnings.append(f"The newest generated content was written by '{last_exp['provider']}', not AMD.")
    if kind == "token_factory":
        warnings.append("Endpoint type is Token Factory: do not mention ROCm, Instinct or vLLM unless you host the model yourself.")

    gemma_sentence = ""
    if gemma_on_amd and gemma_amd_calls:
        parts = [f"{n} {'translation' if p == 'translate' else p} call(s)" for p, n in sorted(gemma_amd_calls.items())]
        gemma_sentence = (f"Gemma ({gemma_amd_model or s.gemma_model}), Google DeepMind's open model, was served from the same AMD "
                          f"endpoint ({gemma_host}) and answered {_join(parts)} in the recorded data. ")
    if amd_real["calls"]:
        claim = (f"On AMD: {_join(served_by_amd)}. These ran on the AMD-hosted model {s.amd_model or '(model not set)'} "
                 f"at {amd_host} ({kind.replace('_', ' ')}); this server recorded {amd_real['calls']} successful calls and "
                 f"{amd_real['input_tokens'] + amd_real['output_tokens']:,} tokens. {gemma_sentence}"
                 f"Not on AMD: {_join(not_on_amd)}.")
    else:
        claim = ("No successful call to a non-local AMD endpoint has been recorded yet, so no AMD claim can be made. "
                 "Generate a campaign with AMD configured, then reload this page.")

    items = [
        {"id": "dashboard", "title": "1. Token Factory dashboard screenshot (before and after a campaign)", "kind": "manual",
         "status": "manual", "detail": "Take it on AMD's own page so the usage counter visibly moves. The app cannot do this for you."},
        {"id": "settings_card", "title": "2. Settings screenshot of the AMD card", "kind": "auto",
         "status": "ok" if amd_real["calls"] else "missing",
         "detail": (f"AMD card data is available: {amd_real['calls']} calls, host {amd_host}." if amd_real["calls"]
                    else "No successful non-local AMD call recorded yet.")},
        {"id": "evidence_script", "title": "3. Evidence script output (python scripts/amd_evidence.py)", "kind": "auto",
         "status": ("ok" if ev_file.get("exists") and ev_file.get("successes") and not ev_file.get("endpoint_is_local") else "missing"),
         "detail": (f"evidence/amd_evidence.json: {ev_file.get('successes')} successes, median {ev_file.get('median_latency_ms')} ms, "
                    f"host {ev_file.get('endpoint_host')}." if ev_file.get("exists") and ev_file.get("readable", True)
                    else "evidence/amd_evidence.json not found. Run the script and screenshot its output.")},
        {"id": "demo_label", "title": "4. Generated campaign in the demo video, Home label not Gemma", "kind": "auto",
         "status": "ok" if last_exp and last_exp["provider"] == "amd" and not _is_loopback(last_exp["endpoint_host"] or "") else "missing",
         "detail": (f"Newest content was written by {last_exp['provider']} ({last_exp['model']})." if last_exp
                    else "No generated content recorded yet.")},
        {"id": "readme_statement", "title": "5. A plain statement in the README and slides", "kind": "manual", "status": "manual",
         "detail": "Use the suggested claim below. State exactly what runs on AMD and what does not."},
    ]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "amd": {"configured": bool(amd_host and s.amd_api_key and s.amd_model), "endpoint_host": amd_host or None,
                "endpoint_kind": kind, "model": s.amd_model or None, "real_calls": amd_real["calls"],
                "input_tokens": amd_real["input_tokens"], "output_tokens": amd_real["output_tokens"],
                "by_purpose": amd_real["by_purpose"], "first_call": amd_real["first"], "last_call": amd_real["last"],
                "local_test_calls": amd_local_calls, "connection_tests": amd_tests,
                "gemma_on_amd": {"enabled": gemma_on_amd, "host": gemma_host or None, "model": gemma_amd_model,
                                 "by_purpose": gemma_amd_calls}},
        "other_text_providers": list(others.values()), "fallback_calls": fallback_calls, "share": share,
        "newest_content_written_by": ({"provider": last_exp["provider"], "model": last_exp["model"], "at": last_exp["ts"]}
                                      if last_exp else None),
        "evidence_file": ev_file, "not_on_amd": not_on_amd, "proof_items": items, "suggested_claim": claim,
        "warnings": warnings,
        "open_question": ("The Evolus track text says 'your own vLLM endpoint on AMD Developer Cloud'. Ask the organisers on "
                          "Discord whether an AMD Token Factory endpoint is accepted."),
        "limits": ("This is what this server observed. It does not prove the endpoint runs on AMD hardware; "
                   "the AMD dashboard does."),
    }


def to_markdown(p: dict) -> str:
    a = p["amd"]
    lines = ["# AMD proof summary", "", f"Generated: {p['generated_at']}", "",
             "## What this server recorded", "",
             f"- AMD endpoint host: {a['endpoint_host'] or 'not set'} ({a['endpoint_kind']})",
             f"- Model: {a['model'] or 'not set'}",
             f"- Successful calls on AMD (workload): {a['real_calls']}  (by purpose: {a['by_purpose'] or 'none'})",
             f"- Tokens on AMD: {a['input_tokens']:,} in / {a['output_tokens']:,} out",
             f"- First / last call: {a['first_call'] or '-'} / {a['last_call'] or '-'}",
             f"- Calls to a local test endpoint (not proof): {a['local_test_calls']}",
             f"- Share of text-generation calls on AMD: {p['share']['amd_calls_pct']}% (tokens {p['share']['amd_output_tokens_pct']}%)",
             f"- Calls answered by a fallback: {p['fallback_calls']}", "",
             "## Suggested statement for the README and slides", "", p["suggested_claim"], "",
             "## The five proof items", ""]
    for i in p["proof_items"]:
        lines.append(f"- [{ {'ok': 'x', 'missing': ' ', 'manual': ' '}[i['status']] }] {i['title']} ({i['status']}): {i['detail']}")
    if p["warnings"]:
        lines += ["", "## Check before recording the demo", ""] + [f"- {w}" for w in p["warnings"]]
    lines += ["", "## Open question", "", p["open_question"], "", "## Limits", "", p["limits"], ""]
    return "\n".join(lines)
