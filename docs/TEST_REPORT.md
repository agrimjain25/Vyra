# Test report

Run on Linux, Python 3.13, headless Chromium (Playwright). **All AI providers were local fake servers** (`backend/tests/mock_providers.py`) that speak the OpenAI-compatible, Gemma generateContent wire formats. These tests show the application logic is correct; they do **not** show that AMD, Google or Evolus work.

| Suite | Command | Result |
|---|---|---|
| Backend (providers, retries, fallback, job dedupe, story graph, safety rules, status, images, security, API, persistence) | `cd backend; python -m pytest -q tests` | **68 passed, 2 skipped** |
| Skipped | `tests/test_live.py` (needs `RUN_LIVE_TESTS=1` and real keys) | **not run** |
| Browser flow | `python scripts\mock_demo.py` then `python tests\e2e\ui_flow.py http://127.0.0.1:8000` | **14/14 checks passed** (create campaign, two distinct experiences, brand checks, honest cost, image loads with naturalWidth>0, story choice advances, honest integration labels, persisted campaign listed, no mobile overflow, no JS errors) |
| Restart persistence | `python tests\e2e\restart_persistence.py` | **passed** |

Screenshots were reviewed by eye for the results page, story player and settings.

## Not run
Live AMD / Gemma / Evolus calls · `scripts/amd_evidence.py` against a real endpoint · `npm install/build` · Docker · Windows PowerShell steps · Firefox/Safari · load testing.

Run the e2e scripts with Playwright installed (`pip install playwright` and a Chromium); they point at the fake-provider demo, not at real services.
