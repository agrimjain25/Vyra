# API

Interactive documentation: run the server and open **http://127.0.0.1:8000/api/docs** (OpenAPI JSON at `/api/openapi.json`).

Errors always look like `{"error":{"code":"…","message":"…","details":…}}`. If `VYRA_API_TOKEN` is set, send it as `X-Vyra-Token` or `Authorization: Bearer`. Expensive routes are rate limited (`RATE_LIMIT_PER_MINUTE`).

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Health check with provider and integration summary |
| GET | `/api/integrations` | Truthful status of every partner technology |
| POST | `/api/integrations/{iid}/test` | Make one tiny real call to verify a provider (costs a little) |
| GET | `/api/brand` | Get the saved brand guide |
| PUT | `/api/brand` | Save the brand guide |
| DELETE | `/api/brand` | Clear the brand guide |
| GET | `/api/jobs/{jid}` | Poll a background job |
| GET | `/api/analytics/summary` | Counts, usage, cost and viewer events from real recorded data |
| GET | `/api/campaigns` | List campaigns |
| POST | `/api/campaigns` | Create a campaign brief |
| GET | `/api/campaigns/{cid}` | Get a campaign with experiences, images and usage |
| PUT | `/api/campaigns/{cid}` | Edit a campaign brief (experiences become stale if content changed) |
| DELETE | `/api/campaigns/{cid}` | Delete a campaign and its images |
| POST | `/api/campaigns/{cid}/duplicate` | Duplicate the brief (no generated content) |
| POST | `/api/campaigns/{cid}/product-image` | Upload an optional product reference image (PNG/JPEG/WebP, max 5 MB) |
| DELETE | `/api/campaigns/{cid}/product-image` | Remove the product reference image |
| POST | `/api/campaigns/{cid}/generate` | Generate content for all (or some) audiences as a background job |
| POST | `/api/campaigns/{cid}/review` | Re-run the brand-safety review (deterministic + optional AI) |
| GET | `/api/experiences/{eid}` | Get one experience with its images |
| PATCH | `/api/experiences/{eid}` | Human edit of headline, copy or CTA (re-runs the fixed-rule review) |
| POST | `/api/experiences/{eid}/regenerate` | Regenerate one audience's experience (background job) |
| POST | `/api/experiences/{eid}/play` | Interactive story: open it, or pick a choice and get the next scene |
| POST | `/api/experiences/{eid}/images` | Generate hero and/or scene images (background job) |
| POST | `/api/images/{iid}/retry` | Retry a failed image |
| POST | `/api/events` | Record a viewer event (CTA click) |
Also: `GET /media/<32 hex>.<png|jpg|webp>` serves stored images. Long operations return a `job` id to poll at `/api/jobs/{id}`.

## Added: Evolus building block and AMD proof (all optional)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/campaigns/{cid}/tone-check` | Saved Evolus Sentiment (AI Tool) results; `{"enabled": false}` when switched off |
| POST | `/api/campaigns/{cid}/tone-check` | Run the Evolus Sentiment AI Tool on every audience (informational only; needs `EVOLUS_SENTIMENT=true`) |
| GET | `/api/proof/amd` | What really ran on the AMD endpoint, computed from recorded calls, plus a suggested README statement |
| POST | `/api/integrations/evolus_tone/test` | One tiny call to the Evolus Sentiment tool (same route as the other tests) |

`GET /api/campaigns/{cid}` also returns a `tone_check` field: `null` unless `EVOLUS_SENTIMENT=true`.
