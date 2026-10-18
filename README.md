# VYRA: adaptive brand experiences

**One brief. A different, interactive, brand-safe experience for every audience.**
Built for the AMD Developer Hackathon: ACT III, Track 4 (A New Kind of Experience), with the Evolus, Google Gemma and Vibe Generation partner prizes.

**Live demo:** https://vyra-203s.onrender.com  |  **Demo video and slides:** see the hackathon submission page
## What it does

Write one campaign brief and a few audiences. VYRA:

1. builds one shared creative strategy and visual identity, so every audience stays on-brand;
2. writes a **different** headline, message, CTA and **branching interactive story** for each audience;
3. generates hero and scene images;
4. **audits its own output**: fixed rules plus an Evolus agent with a brand knowledge library, and a tone check with the Evolus Sentiment AI Tool;
5. translates with Google Gemma, with a guard that flags changed numbers;
6. lets a human edit, regenerate, play the story and approve. "Ready" is advisory: nothing is published automatically.

Every number on screen comes from real stored data. If an AI service fails you see the exact reason; VYRA never fills the gap with sample text.

## How it uses each technology

| Partner | What it does in VYRA |
|---|---|
| **AMD** (required) | An open model served on AMD infrastructure (DeepSeek-V4-Flash via the AMD Token Factory endpoint) writes the strategy and every audience experience, and backs up the review. Settings shows an "AMD proof for judges" card; run `python scripts/amd_proof_pack.py` for `evidence/amd_proof.md`. |
| **Evolus** | Three building blocks, driven by the Evolus REST and chat APIs: a **brand-review agent** (runs on the AMD-served open model), a **knowledge library** (RAG over `evolus/knowledge_library/`), and the **Sentiment AI Tool**. Setup: `evolus/README.md`. |
| **Google Gemma** | Gemma-4-31B-IT is the main multilingual translator and the fallback for text and brand review. |
| **Cloudflare Workers AI** | FLUX.1-schnell generates images (Pollinations is the automatic backup for a failed image). |

## Quick start (Windows PowerShell)

You need Python 3.11 or newer.

```powershell
cd path\to\Vyra
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r backend\requirements.txt
copy backend\.env.example backend\.env
notepad backend\.env
python backend\run.py
```

Open http://127.0.0.1:8000. The same server hosts the app, the API (`/api/docs`) and the generated images.
If PowerShell blocks the activate script, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once.

**Try it without any keys** (fake local AI, canned text, clearly labelled "local test endpoint"):
```powershell
python scripts\mock_demo.py
```

### Docker
```
docker compose up --build
```
Create `backend/.env` first. The container serves the app on port 8000 and keeps data in the `/data` volume. For a host such as Render, skip `.env` and set the variables below in the host's environment settings.

## Configuration (environment variables)

Never commit keys. They live in `backend/.env` (ignored by git) or in your host's environment settings.

| Goal | Variables |
|---|---|
| AMD-hosted text model (required) | `AMD_BASE_URL`, `AMD_API_KEY`, `AMD_MODEL` (we ran `DeepSeek-V4-Flash`) |
| Gemma (translation, fallback) | `GEMMA_MODEL`, and either `GEMMA_OPENAI_BASE_URL` + `GEMMA_OPENAI_API_KEY` (Gemma on the AMD endpoint) or `GEMMA_API_KEY` (Google AI Studio); `FALLBACK_LLM_PROVIDER=gemma` |
| Images | `CF_ACCOUNT_ID`, `CF_API_TOKEN`; backup `POLLINATIONS_API_KEY` |
| Evolus review agent | `EVOLUS_API_KEY`, `EVOLUS_APP_ID`, `EVOLUS_AGENT_ID`, `REVIEW_LLM_PROVIDER=evolus` |
| Evolus Sentiment AI Tool | `EVOLUS_SENTIMENT=true` |
| Show cost in dollars | `AMD_PRICE_INPUT_PER_MTOK_USD`, `AMD_PRICE_OUTPUT_PER_MTOK_USD`, `CF_IMAGE_COST_PER_IMAGE_USD` (cost shows "unknown" until set; VYRA never guesses) |
| Protect a public server | `IMAGE_MAX_PER_DAY`, `RATE_LIMIT_PER_MINUTE`, optional `VYRA_API_TOKEN` |

After starting, open **Settings** and press **Test connection** on each service. A service is labelled "Verified working" only after a real call to a non-local endpoint succeeded.

## Using the app

1. **Create Campaign**: product, audiences, brand rules (facts you can prove, words to avoid, an approved discount if any), review.
2. **Generate with AI**: progress is shown and results appear per audience.
3. Read the **Brand checks**, edit text, play each story, generate images, translate, regenerate one audience if needed.
4. **Ad Preview** shows the hero image as an ad. **Campaign Stats** shows real usage, tokens, latency and cost.

A campaign is "Ready" only when every audience generated and no blocking issue remains; otherwise it shows "Draft" or "Needs review", with the reason.

## Responsible AI

- Deterministic rules catch unsupported claims, numbers not in the brief, unapproved discounts or urgency, personal data and sensitive-attribute targeting.
- The AI-assisted review (Evolus agent) must quote the exact text for every finding; findings without evidence are dropped. It is probabilistic and labelled as such.
- Translations carry a "Translated by Gemma" label and a banner if numbers differ from the original.
- Neither layer is legal advice or a compliance guarantee: a human approves anything that is published.

## Project layout

```
index.html            the app                      public/          favicon, config.js
backend/app/          API, providers, services     backend/tests/   automated tests + fake AI servers
evolus/               knowledge library + setup    scripts/         mock demo, AMD proof tools
docs/                 architecture, API, demo script, limitations, test report
```

## Testing and known limits

- 68 automated backend tests run against local fake AI servers that speak the real wire formats: `cd backend && python -m pytest -q tests`.
- The live services (AMD, Gemma, Evolus, Cloudflare) were exercised through the app's Settings checks and real campaign runs during the hackathon.
- Docker and the optional Vite dev server are included but the main tested path is local Python.
- Single-user prototype: SQLite, in-process background jobs, no accounts. Evolus workflows, the chat widget, video and social publishing are not implemented. See `docs/LIMITATIONS.md`.

## License

MIT, see `LICENSE`.
