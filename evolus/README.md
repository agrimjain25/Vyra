# Evolus building blocks for VYRA

The Evolus track asks for at least two building blocks, driven from the app through the Evolus APIs.
This project uses these two, on top of the review agent:

| # | Building block | Where it lives | How the app uses it |
|---|---|---|---|
| 1 | **Knowledge library** (RAG) | Created in the Evolus workspace from the files in `evolus/knowledge_library/` and attached to the review agent | The app's brand review calls the agent (`REVIEW_LLM_PROVIDER=evolus`) through the OpenAI-compatible chat endpoint; the agent answers from the library |
| 2 | **AI Tool: Sentiment** | Evolus built-in tool | The app calls `POST /api/v1/ai-tools/{tool}/run` for each audience's headline and message (`EVOLUS_SENTIMENT=true`), code in `backend/app/services/evolus_blocks.py` |

## Part A: knowledge library (about 30 minutes, no code)
1. Edit the four files in `knowledge_library/` so the tone, facts, banned words and approved discount match the brand you demo.
2. In your Evolus workspace (app.evolus.ai) open *Knowledge libraries*, create "VYRA Brand Rules" and upload the four files. Wait for indexing.
3. Open your review agent, attach the library, and add the one line from `AGENT_INSTRUCTION_ADDON.md` to its instructions.
4. In `backend/.env` set `REVIEW_LLM_PROVIDER=evolus` and the three `EVOLUS_*` values.
5. Test in the Evolus chat first (is the reply still one JSON object? does it name a library document?), then in VYRA: Settings, Evolus agent card, *Test connection*, then generate a campaign.

If the review fails, VYRA falls back to Gemma, then AMD, so the app keeps working.

## Part B: Sentiment AI Tool
1. In `backend/.env` set `EVOLUS_SENTIMENT=true` (the key and application id from Part A are reused).
2. Restart the backend. Settings now shows an "Evolus AI Tool (Sentiment)" card: press *Test connection*.
3. If the test fails with 400 or 404, open the interactive API reference in your Evolus workspace and compare it with the table below.

| Setting | Default | Change it when |
|---|---|---|
| `EVOLUS_REST_BASE_URL` | `https://api.evolus.ai/api/v1` | the reference shows a different base URL |
| `EVOLUS_SENTIMENT_TOOL` | `sentiment` | the tool's id in the reference is different |
| `EVOLUS_TOOL_INPUT_FIELD` | `text` | the request body names the input differently |
| `EVOLUS_TOOL_EXTRA_JSON` | empty | the tool needs more body fields, e.g. `{"language":"en"}` |

Honest limits: the exact body and reply of the Sentiment tool were not available when this was written, so these four
settings exist. The raw reply is stored with every result so you can see what Evolus returned. The result is
informational: it never changes the brand checks and never blocks "Ready".

## Proof for the Evolus team
After generating a campaign, take a screenshot of the Evolus workspace usage page (API calls, connected model, attached
library). Also check which model the agent runs on: it must be an open model served on AMD, not a closed hosted model.

## Part C: translation fallback agent (optional, about 10 minutes)
Gemma is VYRA's main translator. If Gemma fails, an Evolus agent translates instead.
1. In your Evolus workspace create a second agent, "VYRA Translator", on the same AMD model as the review agent.
2. Instructions for it: "You translate marketing copy. The user message contains INPUT_JSON. Translate only the text values into target_language, keep every id and key unchanged, add no claims, and reply with one JSON object only."
3. Do not attach the brand-review library to it. Copy its agent id into `EVOLUS_TRANSLATOR_AGENT_ID` in `backend/.env`.
4. If you skip this, the fallback uses the review agent, which may answer in the review format. A dedicated agent is safer.
