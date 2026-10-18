# Integration matrix

States shown in *Settings* are computed from recorded calls. "Verified working" needs a successful call to a non-local host.

| Technology | Role in VYRA | Implemented | Verified here? | You must supply |
|---|---|---|---|---|
| **AMD** hosted model | Primary LLM: strategy, per-audience experiences, AI review (`providers/openai_compat.py`, OpenAI-compatible client) | Yes | **No** — only against a local fake server | `AMD_BASE_URL`, `AMD_API_KEY`, `AMD_MODEL` from AMD Token Factory / Developer Cloud |
| **Gemma** (Google open model) | Main multilingual translator (Evolus agent is the translation fallback); also fallback for the brand review (after Evolus) and for text generation (after AMD). Never used for images. `providers/gemma.py`, `services/translate.py` | Yes | **No** (needs your key) | `GEMMA_API_KEY` (a Google AI Studio key works), `GEMMA_MODEL` |
| **Pollinations** image | All image generation (`providers/pollinations.py`). A product reference photo is not sent (Pollinations only accepts public URLs) | Yes, from published docs | **No** | `POLLINATIONS_API_KEY` |
| **Evolus** agent | Optional reviewer via its OpenAI-compatible chat endpoint | Chat endpoint only | **No** | `EVOLUS_API_KEY`, `EVOLUS_APP_ID`, `EVOLUS_AGENT_ID` |
| **Evolus knowledge library** | Brand rules documents attached to the review agent inside the Evolus workspace (`evolus/knowledge_library/`) | Files + instructions only; the app reaches it through the agent chat call | **No** | A library created in your Evolus workspace |
| **Evolus AI Tool: Sentiment** | Optional tone check on each audience's headline and message, through `POST /api/v1/ai-tools/{tool}/run` (`backend/app/services/evolus_blocks.py`) | Yes, off by default (`EVOLUS_SENTIMENT=true`); request body assumed, configurable | **No** | `EVOLUS_API_KEY`, `EVOLUS_APP_ID` |
| Evolus workflows, document extractors, sub-agents, MCP connectors, chat widget | — | **Not implemented** | — | — |
| **PwC Italy** | Sponsor; no technical interface in the event material | **Not implemented**; no claim of partnership | — | — |
| **Vibe Generation** track | The product concept; criteria mapped in `ELIGIBILITY_CHECKLIST.md` | Documentation / whole app | — | — |

## AMD notes
- The adapter can record host, model, latency and token counts per call (`provider_calls`, shown in Settings and Campaign Stats) and `scripts/amd_evidence.py` saves them to JSON.
- It **cannot prove the endpoint runs on AMD hardware**. That proof is the AMD-provided endpoint plus the AMD console/dashboard. Set `AMD_ENDPOINT_KIND` to what you actually use (`token_factory`, `vllm_amd_cloud`, …); the UI repeats it as "declared", not "verified".
- If the track requires the *image* model or other parts to run on AMD too, check the official rules: VYRA's image path uses Cloudflare Workers AI, with Pollinations as an automatic backup.
- I could not check the Token Factory API; if it differs from OpenAI-style `/chat/completions`, `openai_compat.py` is the single file to adjust. `AMD_JSON_MODE` can be set to `none` if JSON mode is rejected.

## Switching providers
`PRIMARY_LLM_PROVIDER`, `FALLBACK_LLM_PROVIDER`, `REVIEW_LLM_PROVIDER` accept `amd`, `gemma`, `evolus` (fallback/review also `none`). Images: Cloudflare first; if it fails, Pollinations is used for that image only.

## Evolus building blocks (added)
See `evolus/README.md`. Two blocks on top of the agent: a **knowledge library** (set up in the Evolus workspace) and the **Sentiment AI Tool** (called from the app). The Sentiment request body and reply shape were not available when it was written, so `EVOLUS_SENTIMENT_TOOL`, `EVOLUS_TOOL_INPUT_FIELD` and `EVOLUS_TOOL_EXTRA_JSON` exist to match your workspace's API reference. The Evolus track also needs the agent's own model to be an open model served on AMD; this app cannot set up or verify that.
