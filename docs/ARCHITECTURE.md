# Architecture

```
Browser (index.html, vanilla JS, hash routes)
   │  JSON over /api, images over /media/<random>.png
   ▼
Starlette app (backend/app/main.py)
   middleware: request-id/log redaction → security headers → body-size limit → optional token → CORS
   api/        campaigns · experiences · system (health, integrations, brand, jobs, analytics, media)
   services/   generation · images · safety · story · prompts · integrations · analytics · jobs · media
   providers/  hub (chain, retries, validation) · openai_compat (AMD / Evolus client) · gemma (translation + text fallback) · services/translate.py · cloudflare (images) · pollinations (image backup)
   db/repo     SQLite (WAL), versioned migrations
```

## Generation flow
1. `POST /api/campaigns/{id}/generate` creates a persisted **job** and returns at once (a duplicate request returns the running job; the database enforces this with a unique index).
2. **Strategy** — one LLM call builds a shared creative platform and visual identity so all audiences stay on-brand.
3. **Experiences** — one call per audience, run in parallel (bounded by `LLM_CONCURRENCY`). Output is validated against a schema, including the story graph (3–8 scenes, 2–3 distinct choices per non-ending scene, at least 2 endings, every scene reachable, no cycles). Invalid output triggers a bounded correction retry, then the fallback provider if one is set, then a visible failure.
4. **Review** — fixed rules plus an optional AI review (see below). Status is computed, never hand-set.
5. **Images** — on request. Rows are unique per (experience, scene, aspect ratio); existing images are reused unless forced; a daily cap (`IMAGE_MAX_PER_DAY`) limits spend; failed images can be retried.

Jobs are stored in SQLite; jobs still "running" at process start are marked failed ("interrupted by restart"), so nothing hangs forever.

## Brand safety
*Fixed rules* (deterministic): prohibited phrases; claims not backed by a verified fact; discounts, prices and urgency not matching the approved offer; unapproved numbers; personal data patterns; sensitive-attribute targeting; audience differentiation (word overlap); tone heuristics.
*AI review*: a model lists findings with quoted evidence; findings whose quote is not in the text are dropped. It is probabilistic and labelled as such.
Status (computed, never hand-set): **Draft** if any audience lacks up-to-date content; **Needs review** if the review has not run or has a blocking (error) finding; **Ready** otherwise (warnings are listed but do not block). `REQUIRE_AI_REVIEW=true` keeps the status at Needs review unless the AI review actually ran. Human edits re-run the fixed rules immediately and mark the AI review stale.

## Provider layer
`ProviderHub.run_json` walks the chain primary → fallback, retries transient errors (rate limit, timeout, 5xx), re-prompts once with the validation error when JSON is malformed, and records every attempt in `provider_calls` (provider, model, host, latency, tokens, cost if priced, error code). Error codes: `auth_failed, quota_exhausted, rate_limited, timeout, unreachable, provider_unavailable, blocked, bad_response, truncated, unsupported_model, missing_credentials`. Messages never contain secrets.

## Data model
`brand_guide, campaigns, audiences, experiences, images, jobs, events, provider_calls`. Deleting a campaign deletes its rows and image files.

## Story playback
The server owns the graph. `POST /api/experiences/{id}/play` returns the scene text and choice labels only; the target scene of each choice is hidden and resolved server-side, and CTA clicks are recorded as events.

## Honest status
`/api/integrations` computes each state from recorded calls (see `INTEGRATIONS.md`). Calls to localhost can never produce "verified".
