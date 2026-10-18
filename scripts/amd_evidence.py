"""Collect REAL evidence that the configured AMD endpoint answers. Writes evidence/amd_evidence.json.

Run from the project root after filling backend/.env:   python scripts/amd_evidence.py
Nothing is invented: every number is measured from the calls made here. If the endpoint host is local
(localhost), the file says so and must NOT be used as AMD proof. This script cannot prove the endpoint runs on AMD
hardware; it records the host, model and timings so you can show them next to the AMD console/dashboard.
"""
import asyncio, json, sys, time, statistics
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
from app.config import Settings  # noqa: E402
from app.providers.base import ProviderError, is_loopback  # noqa: E402
from app.services.prompts import ping_prompt  # noqa: E402

N = int(sys.argv[1]) if len(sys.argv) > 1 else 5


async def main() -> int:
    s = Settings.from_env()
    try:
        from app.state import build_state
        s.database_path = ROOT / "evidence" / "scratch.db"
        s.database_path.parent.mkdir(exist_ok=True)
        state = build_state(s)
        prov = state.hub.llms["amd"]
    except Exception as exc:  # noqa: BLE001
        print("Could not set up the AMD provider:", exc); return 2
    ok, why = prov.configured()
    if not ok:
        print("AMD is not configured:", why, "\nFill AMD_BASE_URL, AMD_API_KEY and AMD_MODEL in backend/.env."); return 2
    system, user = ping_prompt()
    runs = []
    for i in range(N):
        t = time.time()
        try:
            r = await prov.complete(system, user, json_mode=True, max_tokens=60, temperature=0, timeout=s.llm_timeout_s)
            runs.append({"ok": True, "latency_ms": r.latency_ms, "input_tokens": r.input_tokens, "output_tokens": r.output_tokens,
                         "model": r.model, "request_id": r.request_id})
        except ProviderError as exc:
            runs.append({"ok": False, "code": exc.code, "message": exc.message})
    good = [r for r in runs if r["ok"]]
    host = prov.endpoint_host
    doc = {
        "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "endpoint_host": host, "endpoint_is_local": is_loopback(host), "declared_endpoint_kind": s.amd_endpoint_kind,
        "configured_model": prov.model, "calls": runs, "successes": len(good), "failures": len(runs) - len(good),
        "median_latency_ms": int(statistics.median(r["latency_ms"] for r in good)) if good else None,
        "caveat": "Measured from this machine. This proves an OpenAI-compatible endpoint answered; it does not by itself "
                  "prove the endpoint runs on AMD hardware. Pair it with the AMD Developer Cloud / Token Factory console.",
    }
    out = ROOT / "evidence" / "amd_evidence.json"
    out.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    print(json.dumps({k: doc[k] for k in ("endpoint_host", "endpoint_is_local", "configured_model", "successes", "failures", "median_latency_ms")}, indent=2))
    print("Saved", out)
    if doc["endpoint_is_local"]:
        print("WARNING: local endpoint; this is NOT AMD evidence.")
    return 0 if good else 1


sys.exit(asyncio.run(main()))
