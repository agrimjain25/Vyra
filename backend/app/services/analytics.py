"""Analytics computed only from rows that really exist (campaigns, provider calls, viewer events)."""
from __future__ import annotations

from ..repo import Repo


def summary(repo: Repo) -> dict:
    q = repo.db.query
    campaigns = {r["status"]: r["n"] for r in q("SELECT status, COUNT(*) n FROM campaigns GROUP BY status")}
    exps = {r["status"]: r["n"] for r in q("SELECT status, COUNT(*) n FROM experiences GROUP BY status")}
    imgs = {r["status"]: r["n"] for r in q("SELECT status, COUNT(*) n FROM images GROUP BY status")}
    calls = q("""SELECT purpose, provider, model, COUNT(*) calls, SUM(ok) ok_calls,
                 SUM(COALESCE(input_tokens,0)) in_tok, SUM(COALESCE(output_tokens,0)) out_tok,
                 SUM(cost_usd) cost, SUM(CASE WHEN ok=1 AND cost_usd IS NULL THEN 1 ELSE 0 END) unpriced,
                 AVG(CASE WHEN ok=1 THEN latency_ms END) avg_ms
                 FROM provider_calls GROUP BY purpose, provider, model ORDER BY purpose, provider""")
    usage = [{"purpose": c["purpose"], "provider": c["provider"], "model": c["model"], "calls": c["calls"],
              "ok_calls": c["ok_calls"] or 0, "input_tokens": c["in_tok"], "output_tokens": c["out_tok"],
              "cost_usd": round(c["cost"], 6) if c["cost"] is not None else None,
              "calls_without_known_price": c["unpriced"] or 0,
              "avg_latency_ms": round(c["avg_ms"]) if c["avg_ms"] is not None else None} for c in calls]
    priced = [u["cost_usd"] for u in usage if u["cost_usd"] is not None]
    unpriced_calls = sum(u["calls_without_known_price"] for u in usage)

    ev = {r["type"]: r["n"] for r in q("SELECT type, COUNT(*) n FROM events GROUP BY type")}
    sessions = q("SELECT COUNT(DISTINCT session_id) n FROM events")[0]["n"]
    per_exp = q("""SELECT ev.experience_id, ev.audience_name, c.name campaign,
                   SUM(ev.type='experience_opened') opens, SUM(ev.type='choice_selected') choices,
                   SUM(ev.type='story_completed') completions, SUM(ev.type='cta_clicked') cta_clicks
                   FROM events ev LEFT JOIN campaigns c ON c.id = ev.campaign_id
                   GROUP BY ev.experience_id, ev.audience_name, c.name ORDER BY opens DESC""")
    choice_rows = q("""SELECT experience_id, audience_name, scene_id, choice_id, COUNT(*) n FROM events
                       WHERE type='choice_selected' GROUP BY experience_id, scene_id, choice_id""")
    per_experience = [{
        "experience_id": r["experience_id"], "audience": r["audience_name"], "campaign": r["campaign"],
        "opens": r["opens"], "choices": r["choices"], "completions": r["completions"], "cta_clicks": r["cta_clicks"],
        "completion_rate": round(r["completions"] / r["opens"], 3) if r["opens"] else None} for r in per_exp]

    ready = exps.get("ready", 0)
    return {
        "has_data": bool(sum(campaigns.values())),
        "campaigns": {"total": sum(campaigns.values()), "by_status": campaigns},
        "experiences": {"by_status": exps, "ready": ready},
        "images": {"by_status": imgs, "succeeded": imgs.get("succeeded", 0)},
        "usage": usage,
        "cost": {
            "known_usd": round(sum(priced), 6) if priced else None,
            "average_per_ready_experience_usd": round(sum(priced) / ready, 6) if priced and ready else None,
            "complete": unpriced_calls == 0 and bool(priced),
            "note": ("Cost is shown only for calls with a configured price (AMD_PRICE_*, GEMMA_PRICE_*, "
                     "CF_IMAGE_COST_PER_IMAGE_USD / POLLINATIONS_IMAGE_COST_PER_IMAGE_USD). Calls without a configured price are not estimated."),
        },
        "viewer": {"has_events": bool(ev), "sessions": sessions, "events": ev, "per_experience": per_experience,
                   "choice_counts": choice_rows},
    }
