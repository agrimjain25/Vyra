# Gemma in VYRA

Gemma is Google DeepMind's open-weights model family. It is a different model from Gemini (Gemini is closed and hosted only by Google).

| Job | Main | Fallback |
|---|---|---|
| Multilingual translation of an experience (headline, message, CTA, story scenes) | Gemma | Evolus agent |
| AI-assisted brand review | Evolus agent | Gemma, then AMD |
| Strategy and audience experiences | AMD-hosted model | Gemma |
| Images | Cloudflare (Pollinations backup) | not used by Gemma |

## Route 1: AMD Token Factory (Gemma served from the AMD endpoint)
`GET <AMD_BASE_URL>/models` lists `Gemma-4-31B-IT`. In `backend/.env` set `GEMMA_OPENAI_BASE_URL` to the same base URL as `AMD_BASE_URL`,
`GEMMA_OPENAI_API_KEY` to the same key as `AMD_API_KEY`, and `GEMMA_MODEL=Gemma-4-31B-IT`. Because the host equals the AMD host,
the AMD proof panel counts Gemma's calls as AMD-served and mentions them in the suggested claim. The Google API is not used.

## Route 2: Google AI Studio API (Gemma served by Google, not AMD)
1. Get a key at https://aistudio.google.com/apikey (free to create). Put it in `backend/.env` as `GEMMA_API_KEY`.
2. Check the exact model id: open `https://generativelanguage.googleapis.com/v1beta/models?key=YOUR_KEY` or the AI Studio model list, and copy a name starting with `gemma-` into `GEMMA_MODEL` (default `gemma-4-31b-it`).
3. Restart the backend, open Settings, press *Test connection* on the Gemma card.
4. Open a generated campaign, choose a language under *Multilingual versions* and press *Translate*.

On the Google API route, Gemma runs on Google's servers, not on AMD hardware. To run Gemma on AMD, serve it with vLLM on AMD Developer Cloud and set `GEMMA_OPENAI_BASE_URL`, `GEMMA_OPENAI_API_KEY` and `GEMMA_MODEL`; VYRA then uses an OpenAI-compatible client for it.

If the API rejects a system instruction or JSON mode for Gemma, VYRA detects that and retries without it.
