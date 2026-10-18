"""Brand-safety review.

Two layers, always reported separately:

* **Deterministic checks** - fixed rules (regexes and comparisons). Same input, same output.
* **AI-assisted checks** - a language model reads the brief and the generated text and reports problems.
  This is probabilistic: it can miss things and it can be wrong. It is not a legal or compliance review.

Campaign status rules (see ``compute_status``) are explicit and documented in the README.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any

from ..config import Settings
from ..providers.hub import GenerationFailed, ProviderHub
from ..schemas import AIReviewOut
from .prompts import review_prompt

DISCLAIMER = ("Deterministic checks are fixed rules and only catch what they are written to catch. AI-assisted checks "
              "are probabilistic and can miss or invent issues. Neither is legal advice or a compliance guarantee: "
              "a human should approve anything that is published.")

# --------------------------------------------------------------------------- rule tables

# (rule name, pattern, severity if no verified fact contains the same term)
CLAIM_RULES: list[tuple[str, re.Pattern, str]] = [
    ("sustainability claim", re.compile(
        r"\b(sustainab\w*|eco[- ]?friendly|eco[- ]?conscious|carbon[- ]neutral|net[- ]zero|biodegradab\w*|compostable|"
        r"planet[- ]friendly|zero[- ]waste|climate[- ]positive|environmentally[- ]friendly)\b", re.I), "error"),
    ("material or ingredient claim", re.compile(
        r"\b(recycled|upcycled|organic|vegan|non[- ]toxic|bpa[- ]free|cruelty[- ]free|fair[- ]trade|hypoallergenic)\b", re.I), "error"),
    ("health or scientific claim", re.compile(
        r"\b(clinically|scientifically|dermatologist\w*|doctor[- ]recommended|cures?|cured|heals?|healing|treats?|"
        r"treatment|prevents?|therapeutic|medical[- ]grade)\b", re.I), "error"),
    ("proof or endorsement claim", re.compile(
        r"\b(proven|guarantee[sd]?|certified|lab[- ]tested|award[- ]winning|patented)\b", re.I), "error"),
    ("performance claim", re.compile(
        r"\b(waterproof|water[- ]resistant|windproof|shockproof|indestructible|unbreakable|lifetime warranty)\b", re.I), "error"),
    ("superlative or comparative claim", re.compile(
        r"(#1|\bno\.? ?1\b|\b(?:best|fastest|lightest|strongest|ultimate|unbeatable|unmatched|unrivall?ed|"
        r"number one|leading|revolutionary|breakthrough|perfect|flawless|miracle|game[- ]chang\w*|"
        r"world'?s (?:first|best|most))\b)", re.I), "warning"),
    ("durability claim", re.compile(r"\b(durable|long[- ]lasting|tested)\b", re.I), "warning"),
]

DISCOUNT_RE = re.compile(
    r"(\b\d{1,3}\s?%\s?(?:off|discount)\b|\bdiscount(?:s|ed)?\b|\bsale\b|\bpromo(?:tion|\s?code)?\b|\bcoupon\b|"
    r"\bfree (?:shipping|delivery|gift|trial)\b|\bbogo\b|\bbuy one,? get one\b|\bsave (?:up to )?[$€£]?\d+|"
    r"\bhalf[- ]price\b|\bclearance\b|\bspecial offer\b|\bon offer\b)", re.I)
PRICE_RE = re.compile(r"[$€£]\s?\d[\d.,]*|\b\d[\d.,]*\s?(?:usd|eur|gbp|dollars|euros|pounds)\b", re.I)
URGENCY_RE = re.compile(
    r"\b(limited[- ]time|while (?:stocks?|supplies) last|last chance|ends (?:today|soon|tonight)|hurry|only \d+ left|"
    r"today only|don'?t miss out|act now|selling fast)\b", re.I)

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE_RE = re.compile(r"(?<![\w.])\+?\d(?:[\s().-]?\d){8,14}(?![\w])")

_SENSITIVE = (r"diabet\w*|depress\w*|anxiety|cancer|hiv|pregnan\w*|disabilit\w*|disabled|mental[- ]health|obes\w*|"
              r"overweight|religio\w*|muslim|christian|jewish|hindu|buddhist|ethnic\w*|racial|lgbt\w*|gay|lesbian|"
              r"transgender|sexual orientation|immigrant|criminal record|bankrupt\w*|low[- ]income|in debt|unemployed")
SENSITIVE_RE = re.compile(rf"\b({_SENSITIVE})\b", re.I)
INFER_RE = re.compile(
    rf"\b(?:you(?:'re| are| have| suffer(?:ing)? from| struggle(?:s)? with)|as a|for those (?:with|who (?:suffer|struggle)))\b"
    rf"[^.!?\n]{{0,60}}?\b({_SENSITIVE})\b", re.I)

NUM_RE = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)?")
STOP = set("this that with your from have they them will what when where which their about into more than then also "
           "been were just only like make made every each other such here there these those over under while".split())


def _flat(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _tokens(s: str) -> set[str]:
    return {w for w in re.findall(r"[^\W\d_]{4,}", s.lower()) if w not in STOP}


def _jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def _snippet(text: str, m: re.Match, pad: int = 28) -> str:
    start, end = max(0, m.start() - pad), min(len(text), m.end() + pad)
    return ("…" if start else "") + text[start:end].replace("\n", " ") + ("…" if end < len(text) else "")


def _finding(check: str, title: str, severity: str, message: str, *, audience: str | None = None,
             field: str | None = None, evidence: str = "", suggestion: str = "", source: str = "deterministic") -> dict:
    fid = hashlib.sha1(f"{source}|{check}|{audience}|{field}|{evidence}".encode()).hexdigest()[:10]
    return {"id": fid, "check": check, "title": title, "severity": severity, "source": source, "audience": audience,
            "field": field, "message": message, "evidence": evidence, "suggestion": suggestion}


# --------------------------------------------------------------------------- text helpers

def customer_texts(exp: dict) -> list[tuple[str, str]]:
    """The text a real customer would read, as (field, text)."""
    out = [("headline", exp["headline"]), ("body_copy", exp["body_copy"]), ("cta", exp["cta"])]
    for s in exp["scenes"]:
        out.append((f"scene:{s['id']}", f"{s.get('title', '')}. {s.get('narration', '')}"))
        for c in s.get("choices", []):
            out.append((f"scene:{s['id']}", c["label"]))
    return out


def _allowed_numbers(campaign: dict) -> set[str]:
    blob = " ".join([campaign["brand"], campaign["product"], campaign["name"], campaign["description"],
                     campaign["approved_discount"] or "", *campaign["facts"]])
    return {n.replace(",", ".") for n in NUM_RE.findall(blob)}


# --------------------------------------------------------------------------- deterministic review

def deterministic_review(campaign: dict, audiences: list[dict], experiences: list[dict]) -> list[dict]:
    findings: list[dict] = []
    by_aud = {a["id"]: a for a in audiences}
    facts_flat = [_flat(f) for f in campaign["facts"]]
    facts_blob = " ".join(campaign["facts"]).lower()
    approved = campaign["approved_discount"] or ""
    approved_nums = {n.replace(",", ".") for n in NUM_RE.findall(approved)}
    allowed_nums = _allowed_numbers(campaign)

    if not campaign["facts"]:
        findings.append(_finding(
            "no_verified_facts", "No verified facts supplied", "info",
            "No verified product facts were provided, so any factual claim in the copy is unsupported. "
            "Add facts you can prove to get more specific copy."))

    # audience definitions
    for a in audiences:
        blob = " ".join([a["interests"], a["needs"], a["context"], a["name"]])
        if EMAIL_RE.search(blob) or PHONE_RE.search(blob):
            findings.append(_finding("personal_data", "Personal data in audience", "error",
                                     "The audience description contains an email address or phone number. "
                                     "Describe groups by interests and needs, never by personal details.",
                                     audience=a["name"], field="audience"))
        m = SENSITIVE_RE.search(blob)
        if m:
            findings.append(_finding("sensitive_audience", "Sensitive category in audience", "warning",
                                     "The audience definition mentions a sensitive category. Avoid targeting or "
                                     "inferring sensitive attributes.", audience=a["name"], field="audience",
                                     evidence=_snippet(blob, m)))

    seen: set[tuple] = set()

    def add(f: dict) -> None:
        key = (f["check"], f["audience"], f["field"], f["evidence"])
        if key not in seen:
            seen.add(key)
            findings.append(f)

    for exp in experiences:
        aud = by_aud.get(exp["audience_id"])
        aname = aud["name"] if aud else None
        for field, text in customer_texts(exp):
            low = text.lower()
            # prohibited phrases (strict substring, case-insensitive)
            for phrase in campaign["avoid"]:
                i = low.find(phrase.lower())
                if i >= 0:
                    m = re.compile(re.escape(phrase), re.I).search(text)
                    add(_finding("prohibited_phrase", f'Prohibited phrase "{phrase}"', "error",
                                 f'The copy uses "{phrase}", which is on the prohibited list.', audience=aname,
                                 field=field, evidence=_snippet(text, m) if m else phrase,
                                 suggestion="Rewrite the sentence without this phrase."))
            # claims that need a verified fact
            for rule, rx, severity in CLAIM_RULES:
                for m in rx.finditer(text):
                    term = m.group(0)
                    key = _flat(term)[:6]
                    if key and any(key in f for f in facts_flat):
                        continue
                    add(_finding("unsupported_claim", f'Unsupported {rule}: "{term}"', severity,
                                 f'The text says "{term}" but no verified fact supports it.', audience=aname,
                                 field=field, evidence=_snippet(text, m),
                                 suggestion="Remove the claim or add a verified fact that supports it."))
            # offers, discounts, prices
            for m in DISCOUNT_RE.finditer(text):
                term = m.group(0)
                if not approved:
                    add(_finding("unapproved_offer", f'Offer language without an approved discount: "{term}"', "error",
                                 "The copy mentions a discount or promotion, but no approved discount was entered.",
                                 audience=aname, field=field, evidence=_snippet(text, m),
                                 suggestion="Remove the offer or enter the approved discount in the campaign brief."))
                else:
                    nums = {n.replace(",", ".") for n in NUM_RE.findall(term)}
                    if nums and not nums <= approved_nums:
                        add(_finding("discount_mismatch", f'Discount differs from the approved offer: "{term}"', "error",
                                     f'Approved discount is "{approved}", but the copy states "{term}".',
                                     audience=aname, field=field, evidence=_snippet(text, m),
                                     suggestion="Use exactly the approved discount."))
            for m in PRICE_RE.finditer(text):
                term = m.group(0)
                if term.lower() not in facts_blob and term.lower() not in approved.lower():
                    add(_finding("unapproved_price", f'Price not found in the brief: "{term}"', "error",
                                 "The copy states a price that is not in the verified facts or approved offer.",
                                 audience=aname, field=field, evidence=_snippet(text, m)))
            for m in URGENCY_RE.finditer(text):
                if m.group(0).lower() not in approved.lower():
                    add(_finding("urgency", f'Urgency wording: "{m.group(0)}"', "warning",
                                 "Urgency or scarcity wording can mislead if it is not true. Confirm it is accurate.",
                                 audience=aname, field=field, evidence=_snippet(text, m)))
            # numbers / statistics
            for m in NUM_RE.finditer(text):
                n = m.group(0).replace(",", ".")
                if n not in allowed_nums:
                    add(_finding("unsupported_number", f'Number not found in the brief: "{m.group(0)}"', "warning",
                                 "Numbers and statistics should come from the verified facts.", audience=aname,
                                 field=field, evidence=_snippet(text, m)))
            # personal data and sensitive inference in the generated text
            if EMAIL_RE.search(text) or PHONE_RE.search(text):
                add(_finding("personal_data", "Personal data in copy", "error",
                             "The generated text contains something that looks like an email address or phone number.",
                             audience=aname, field=field, evidence=text[:80]))
            m = INFER_RE.search(text)
            if m:
                add(_finding("sensitive_inference", "Possible sensitive inference about the viewer", "error",
                             "The text addresses the viewer as having a sensitive attribute.", audience=aname,
                             field=field, evidence=_snippet(text, m)))
            else:
                m = SENSITIVE_RE.search(text)
                if m:
                    add(_finding("sensitive_term", "Sensitive term in copy", "warning",
                                 "The text mentions a sensitive category. Make sure it does not imply knowledge about the viewer.",
                                 audience=aname, field=field, evidence=_snippet(text, m)))

        # length and tone heuristics
        if len(exp["headline"]) > 90:
            add(_finding("headline_length", "Long headline", "warning",
                         f"The headline is {len(exp['headline'])} characters; headlines work best under 70.",
                         audience=aname, field="headline", evidence=exp["headline"][:90]))
        if len(exp["cta"]) > 40:
            add(_finding("cta_length", "Long call to action", "warning",
                         f"The CTA is {len(exp['cta'])} characters.", audience=aname, field="cta", evidence=exp["cta"]))
        main = exp["headline"] + " " + exp["body_copy"]
        excl = main.count("!")
        caps = len([w for w in re.findall(r"\b[A-Z]{4,}\b", main)])
        if campaign["tone"] == "Premium" and (excl > 0 or caps > 1):
            add(_finding("voice_heuristic", "May not read as Premium", "warning",
                         "Premium tone usually avoids exclamation marks and shouting. (Heuristic check.)",
                         audience=aname, field="body_copy", evidence=main[:100]))
        elif campaign["tone"] == "Confident" and excl > 2:
            add(_finding("voice_heuristic", "Too many exclamation marks for a Confident tone", "warning",
                         "Confident copy rarely needs more than one or two exclamation marks. (Heuristic check.)",
                         audience=aname, field="body_copy", evidence=main[:100]))

    # audience differentiation
    ready = [e for e in experiences if e["status"] == "ready"]
    if len(ready) >= 2:
        for i in range(len(ready)):
            for j in range(i + 1, len(ready)):
                a, b = ready[i], ready[j]
                an = by_aud[a["audience_id"]]["name"] if a["audience_id"] in by_aud else "?"
                bn = by_aud[b["audience_id"]]["name"] if b["audience_id"] in by_aud else "?"
                if a["headline"].strip().lower() == b["headline"].strip().lower():
                    sev, why = "error", "have the same headline"
                else:
                    hs = _jaccard(_tokens(a["headline"]), _tokens(b["headline"]))
                    cs = _jaccard(_tokens(a["body_copy"]), _tokens(b["body_copy"]))
                    ss = _jaccard(_tokens(" ".join(s["narration"] for s in a["scenes"])),
                                  _tokens(" ".join(s["narration"] for s in b["scenes"])))
                    if hs >= 0.7 or cs >= 0.6 or ss >= 0.6:
                        sev, why = "warning", f"are very similar (headline {hs:.0%}, copy {cs:.0%}, story {ss:.0%} word overlap)"
                    else:
                        continue
                add(_finding("low_differentiation", "Audiences are not different enough", sev,
                             f'"{an}" and "{bn}" {why}. Personalisation should change the message, not just the label.',
                             audience=an, field="headline", evidence=f"{a['headline']} / {b['headline']}"))
    elif len(experiences) <= 1:
        findings.append(_finding("differentiation_skipped", "Differentiation not checked", "info",
                                 "Add a second audience to compare how different the experiences are."))
    return findings


# --------------------------------------------------------------------------- AI-assisted review

async def ai_review(hub: ProviderHub, campaign: dict, audiences: list[dict], experiences: list[dict]) -> dict:
    """Never raises: returns {status, provider, model, message, findings}."""
    by_aud = {a["id"]: a["name"] for a in audiences}
    ready = [{**e, "audience_name": by_aud.get(e["audience_id"], "?")} for e in experiences if e["status"] == "ready"]
    if not ready:
        return {"status": "skipped", "message": "No generated content to review.", "findings": []}
    if not hub.chain("review"):
        return {"status": "skipped", "message": "No AI provider is selected for review.", "findings": []}
    system, user = review_prompt(campaign, ready)
    try:
        out = await hub.run_json(purpose="review", system=system, user=user, model_cls=AIReviewOut,
                                 campaign_id=campaign["id"], max_tokens=2500, temperature=0.2)
    except GenerationFailed as exc:
        return {"status": "failed", "message": exc.message, "findings": []}
    corpus = {e["audience_name"]: "\n".join(t for _, t in customer_texts(e)) for e in ready}
    all_text = "\n".join(corpus.values()).lower()
    findings = []
    for f in out.parsed.findings[:25]:  # type: ignore[attr-defined]
        severity = f.severity
        message = f.message
        verified = bool(f.evidence) and f.evidence.strip().lower() in all_text
        if f.evidence and not verified:
            severity = "warning"
            message += " (The quoted text was not found verbatim, so this finding is unverified.)"
        findings.append(_finding(
            f"ai_{f.category}", f"AI-assisted: {f.category.replace('_', ' ')}", severity, message,
            audience=f.audience, field=f.field, evidence=f.evidence, suggestion=f.suggestion, source="ai"))
        findings[-1]["by"] = out.provider  # which model really produced this finding (shown in the UI)
    return {"status": "ran", "provider": out.provider, "model": out.model, "message": out.parsed.summary,  # type: ignore[attr-defined]
            "findings": findings, "fallback_used": out.fallback_used}


# --------------------------------------------------------------------------- status rules

def build_review(det: list[dict], ai: dict | None) -> dict[str, Any]:
    findings = list(det) + list((ai or {}).get("findings", []))
    order = {"error": 0, "warning": 1, "info": 2}
    findings.sort(key=lambda f: (order[f["severity"]], f["source"] != "deterministic"))
    ai_meta = {k: v for k, v in (ai or {"status": "skipped", "message": "AI-assisted review was not run."}).items()
               if k != "findings"}
    return {
        "ran_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "deterministic": {"count": len(det)}, "ai": ai_meta, "findings": findings,
        "counts": {s: sum(1 for f in findings if f["severity"] == s) for s in ("error", "warning", "info")},
        "disclaimer": DISCLAIMER,
    }


def compute_status(audiences: list[dict], experiences: list[dict], review: dict | None,
                   settings: Settings) -> tuple[str, str]:
    """Explicit rules:

    draft        - at least one audience has no up-to-date generated experience
    needs_review - everything generated, but the review has not run, or has an error-severity finding
    ready        - everything generated, review ran, no error-severity findings (warnings are listed, not blocking)
    """
    by_aud = {e["audience_id"]: e for e in experiences}
    ready = [a for a in audiences if by_aud.get(a["id"], {}).get("status") == "ready"]
    if not audiences or len(ready) < len(audiences):
        stale = any(e["status"] == "stale" for e in experiences)
        why = ("The brief was edited. Regenerate to refresh the content." if stale
               else f"{len(ready)} of {len(audiences)} audiences have generated content.")
        return "draft", why
    if review is None:
        return "needs_review", "The brand-safety review has not run yet."
    errors = review["counts"]["error"]
    if errors:
        return "needs_review", f"{errors} blocking issue{'s' if errors != 1 else ''} to fix or approve before publishing."
    ai_ran = review.get("ai", {}).get("status") == "ran"
    if settings.require_ai_review and not ai_ran:
        return "needs_review", "AI-assisted review did not run, and this server requires it (REQUIRE_AI_REVIEW)."
    warn = review["counts"]["warning"]
    tail = "" if ai_ran else " AI-assisted review did not run, so only the fixed rules were checked."
    return "ready", (f"All audiences generated, no blocking issues{f', {warn} warning' + ('s' if warn != 1 else '') if warn else ''}."
                     + tail)
