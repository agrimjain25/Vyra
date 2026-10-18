# AMD proof summary

Generated: 2026-10-18T00:33:44+00:00

## What this server recorded

- AMD endpoint host: developer.amd.com.cn (token_factory)
- Model: DeepSeek-V4-Flash
- Successful calls on AMD (workload): 30  (by purpose: {'experience': 14, 'review': 5, 'strategy': 11})
- Tokens on AMD: 40,783 in / 29,018 out
- First / last call: 2026-10-15T20:52:17.717382Z / 2026-10-17T23:12:29.384948Z
- Calls to a local test endpoint (not proof): 0
- Share of text-generation calls on AMD: 75% (tokens 89%)
- Calls answered by a fallback: 3

## Suggested statement for the README and slides

On AMD: the campaign strategy, the per-audience content and story generation and the AI-assisted brand review. These ran on the AMD-hosted model DeepSeek-V4-Flash at developer.amd.com.cn (token factory); this server recorded 30 successful calls and 69,801 tokens. Gemma (self-dploy/Gemma-4-31B-IT), Google DeepMind's open model, was served from the same AMD endpoint (developer.amd.com.cn) and answered 3 experience call(s) and 10 translation call(s) in the recorded data. Not on AMD: image generation (Cloudflare Workers AI, Pollinations as backup) and the model inside the Evolus agent (it answered 7 review call(s); it runs in your Evolus workspace, not through this app's AMD settings).

## The five proof items

- [ ] 1. Token Factory dashboard screenshot (before and after a campaign) (manual): Take it on AMD's own page so the usage counter visibly moves. The app cannot do this for you.
- [x] 2. Settings screenshot of the AMD card (ok): AMD card data is available: 30 calls, host developer.amd.com.cn.
- [x] 3. Evidence script output (python scripts/amd_evidence.py) (ok): evidence/amd_evidence.json: 5 successes, median 1123 ms, host developer.amd.com.cn.
- [x] 4. Generated campaign in the demo video, Home label not Gemma (ok): Newest content was written by amd (self-dploy/DeepSeek-V4-Flash).
- [ ] 5. A plain statement in the README and slides (manual): Use the suggested claim below. State exactly what runs on AMD and what does not.

## Check before recording the demo

- 3 text call(s) were answered by a fallback provider. Check the Home page label before recording the demo: it must not say Gemma.
- Endpoint type is Token Factory: do not mention ROCm, Instinct or vLLM unless you host the model yourself.

## Open question

The Evolus track text says 'your own vLLM endpoint on AMD Developer Cloud'. Ask the organisers on Discord whether an AMD Token Factory endpoint is accepted.

## Limits

This is what this server observed. It does not prove the endpoint runs on AMD hardware; the AMD dashboard does.
