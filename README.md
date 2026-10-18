# VYRA — adaptive brand experiences

Write one campaign brief and a few audiences. VYRA asks a language model to write a **different** message, interactive branching story and image direction for each audience, generates the images, checks the result against your brand rules, and lets a human edit and approve it.

Every number on screen comes from real stored data. If an AI service is missing or fails, you see the exact reason. VYRA never fills the gap with sample text.

> **Read this first — what has and has not been proven.** The code was tested against *local fake* AI servers that speak the real wire formats (68 automated backend tests + a browser run). It has **not** been run against the real AMD, Google or Evolus services, because no keys were available while it was built. The first thing to do after setup is the **Test connection** buttons in *Settings*. See `docs/TEST_REPORT.md` and `docs/LIMITATIONS.md`.

## Quick start (Windows, PowerShell, no npm needed)

You need **Python 3.11 or newer** (https://www.python.org/downloads/ — tick "Add python.exe to PATH").

```powershell
cd path\to\vyra
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```
If PowerShell refuses to run the activate script, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once and try again.

```powershell
pip install -r backend\requirements.txt
copy backend\.env.example backend\.env
notepad backend\.env
```
Fill in the values (see "Keys" below), save, then:
```powershell
python backend\run.py
```
Open **http://127.0.0.1:8000**. The same server hosts the app, the API (`/api/docs` is interactive documentation) and the generated images.

**Try it without any keys** (fake local AI, canned text, clearly labelled "local test endpoint"):
```powershell
pip install -r backend\requirements.txt
python scripts\mock_demo.py
```

### Keys (never paste them into chat, email or git)
All keys live only in `backend\.env`, which stays on your machine and is excluded from the ZIP.

| Goal | Fill in |
|---|---|
| Text with the AMD-hosted model (**required for the AMD track**) | `AMD_BASE_URL`, `AMD_API_KEY`, `AMD_MODEL` — copy these from your AMD Token Factory / AMD Developer Cloud page |
| Images (Cloudflare Workers AI, main) | `CF_ACCOUNT_ID`, `CF_API_TOKEN` |
| Image backup (Pollinations, used automatically only if Cloudflare fails) | `POLLINATIONS_API_KEY` |
| Gemma (Google open model): main translator, plus fallback for review and text (text only, never images) | `GEMMA_API_KEY` (an AI Studio key works), `GEMMA_MODEL`, `FALLBACK_LLM_PROVIDER=gemma` |
| Optional Evolus agent as the reviewer | `EVOLUS_API_KEY`, `EVOLUS_APP_ID`, `EVOLUS_AGENT_ID`, `REVIEW_LLM_PROVIDER=evolus` |
| Show cost in dollars | `AMD_PRICE_INPUT_PER_MTOK_USD`, `AMD_PRICE_OUTPUT_PER_MTOK_USD`, `CF_IMAGE_COST_PER_IMAGE_USD` (cost stays "unknown" until you set prices) |

Then in the app: **Settings → Test connection** on each service. A service is labelled "Verified working" only after a real call to a non-local endpoint succeeded.

To collect AMD evidence for judges: `python scripts\amd_evidence.py` (writes `evidence\amd_evidence.json`).

## Using the app
1. **Create Campaign** — 4 short pages: product, audiences, brand rules (facts you can prove, words to avoid, an approved discount, if any), review.
2. **Generate with AI** — progress is shown; results appear per audience.
3. Read the **Brand checks**, edit text, play each story, generate hero/scene images, regenerate one audience if needed.
4. **Ad Preview** shows the hero image with the headline as an ad. **Campaign Stats** shows real usage, tokens, latency and cost.

A campaign is "Ready" only when every audience generated and no blocking issue remains; otherwise it shows "Draft" or "Needs review", with the reason. "Ready" is a recommendation: a human must still approve anything before it is published.

## Project layout
```
index.html            the app (original look preserved)   public/   favicon, config.js
backend/app/          API, providers, services, SQLite    backend/tests/   automated tests + fake AI servers
scripts/              mock_demo.py, amd_evidence.py       tests/e2e/       browser + restart checks
docs/                 architecture, API, integrations, eligibility, demo script, troubleshooting, limitations, test report
```

## Why Starlette and not FastAPI?
The build environment could not reach PyPI, so the backend uses the Starlette/Pydantic/httpx packages that were available offline, with a small router that gives typed JSON bodies, a uniform error format and OpenAPI docs. The behaviour is the same for you; `pip install -r backend\requirements.txt` fetches everything normally on your PC.

## Optional: Vite dev server or Docker
`npm install` then `npm run dev` (port 5173, proxies to the backend on 8000). `docker compose up --build` after creating `backend/.env`. Neither was run in the build environment.

## Safety defaults
Keys are read server-side only and redacted from logs. Request size, rate limits and image size are capped. Uploaded/generated images are checked by file signature and served only by random names. Set `VYRA_API_TOKEN` if the server is reachable by anyone else. Do not expose it publicly without that.

More: `docs/TROUBLESHOOTING.md`, `docs/DEMO_SCRIPT.md`.

## Added for the submission: Evolus building blocks and AMD proof
- **Evolus building blocks** (knowledge library + Sentiment AI Tool, on top of the review agent): see `evolus/README.md`. The Sentiment tool is off by default; enable it with `EVOLUS_SENTIMENT=true` in `backend/.env`.
- **AMD proof**: Settings has an *AMD proof for judges* card; `python scripts/amd_proof_pack.py` writes `evidence/amd_proof.md`; the five proof items and exactly what to claim are in `docs/AMD_PROOF.md`.
- Both additions are optional and change nothing about generation, images, review or costs when switched off. Not run against the real Evolus or AMD services: use *Test connection* in Settings first.
