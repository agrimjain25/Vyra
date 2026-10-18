# AMD proof for judges

Judges cannot see your `.env`, so you have to show what ran on AMD. VYRA gives you three things: a Settings card,
an endpoint and a script, all computed only from calls this server really recorded.

## The five proof items
1. **Token Factory dashboard screenshot** (AMD's own page). Take one before the demo and one after generating a campaign so the usage counter visibly moves. This is the strongest proof and the app cannot do it for you.
2. **Settings screenshot**: the *AMD (hosted open model)* card (Verified working, endpoint host, model, calls recorded, tokens) and the new *AMD proof for judges* card below the service cards.
3. **Evidence script output**: `python scripts/amd_evidence.py` (non-local host, successes, median latency). Screenshot the terminal.
4. **A generated campaign in the demo video** with the Home line "Written by AMD-hosted model (model name)" visible. If AMD fails the app falls back to Gemma, so check that label does not say Gemma.
5. **A plain statement** in the README and slides. After generating a campaign run `python scripts/amd_proof_pack.py`: it writes `evidence/amd_proof.md` with an exact sentence built from the recorded data.

The same data is available at `GET /api/proof/amd`.

## What to claim, and what not to
- On AMD (when the recorded data shows it): campaign strategy, per-audience content and story generation, and the main AI review.
- Not on AMD: images (Cloudflare Workers AI, Pollinations as backup), Gemma (translation, plus fallback for text and review; on the Google API route it runs on Google servers), and the model inside the Evolus agent unless you connected it to your AMD model in the Evolus workspace.
- Do not write "everything runs on AMD". Do not mention ROCm, Instinct or vLLM unless you host the model yourself on AMD Developer Cloud (`AMD_ENDPOINT_KIND=developer_cloud_vllm`).

## Open question for the organisers
The Evolus track text says "your own vLLM endpoint on AMD Developer Cloud". Ask on Discord whether an AMD Token Factory endpoint is accepted for that rule.

## Limits
The recorded calls prove which endpoint answered, with which model, how fast and how many tokens. They do not prove the
endpoint runs on AMD hardware. That proof is AMD's own dashboard (or your own vLLM server on AMD Developer Cloud).
