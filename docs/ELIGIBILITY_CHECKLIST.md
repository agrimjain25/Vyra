# Eligibility checklist (Track 4 — Vibe Generation)

Status key: ✅ built and tested against fakes · ⚠️ built, needs your credentials/verification · ❌ not done. **Re-read the official rules; this is my reading of the text provided.**

## Mandatory
| Requirement | Status | Notes |
|---|---|---|
| Use AMD infrastructure | ⚠️ | Primary LLM is the AMD adapter. Needs your endpoint; run Test connection and `scripts/amd_evidence.py`. **This is the largest risk: unverified.** |
| Working product, not slides | ✅ | End-to-end flow runs; see `TEST_REPORT.md` |
| Real AI output, no hidden mock | ✅ | Failures are shown; mock server is separate and labelled |

## Optional / sponsors
| Item | Status |
|---|---|
| Google (Gemma: multilingual translator, plus fallback for review and text) |  ⚠️ implemented, needs your key; run Settings → Test connection and translate once |
| Evolus | ⚠️ agent via chat endpoint (reviewer) + knowledge library (set up in workspace) + Sentiment AI Tool (optional, `EVOLUS_SENTIMENT=true`). Not verified live. The agent's own model must be an open model on AMD: check it in the workspace. |
| PwC Italy | ❌ nothing to integrate; no claim made |
| Imment | ❌ no interface in the material; no claim made |

## Track criteria (adaptive, interactive brand experience)
| Criterion | Evidence in the app |
|---|---|
| Different experience per audience | Per-audience headline, copy, CTA, concept, story; similarity check warns when too alike |
| Interactive / experiential | Branching story with server-resolved choices and CTA events |
| Visual generation | Hero + scene images, stored, retryable, ad preview |
| Brand consistency | Shared strategy + visual identity fed to every audience and image prompt |
| Human + AI workflow | Human edits, regeneration, brand checks, "Ready" is advisory |
| Responsible AI | Fixed-rule + AI review with evidence, disclaimers, no invented claims/discounts |
| Cost per experience / who pays | Usage card shows tokens and cost (once prices are set); see `DEMO_SCRIPT.md` for the pitch |
