# Known limitations

- **No live AI verification.** AMD, Gemma, Pollinations and Evolus were never called for real. Gemma and Pollinations request/response shapes follow the docs; the AMD endpoint is assumed OpenAI-compatible.
- **AMD proof is incomplete by design:** the app records what answered, not what hardware ran it. Images use Cloudflare (Pollinations as backup), not AMD.
- **Evolus:** agent chat endpoint, plus an optional Sentiment AI Tool call whose request/response shape is assumed (configurable) and never tested live. The knowledge library is created in the Evolus workspace, not from this app. Workflows, extractors, sub-agents, MCP connectors and the chat widget are not implemented. **PwC Italy / Imment:** nothing implemented or claimed.
- Cost is "unknown" until you configure prices; image cost is unpriced unless `CF_IMAGE_COST_PER_IMAGE_USD` is set. Token counts depend on what the provider reports.
- Model output quality varies; the fixed-rule review catches only what it is written to catch, the AI review can miss or invent issues. A human must approve.
- Single-user prototype: no accounts, in-memory rate limiter, SQLite, background jobs live in one process. Don't expose it publicly without `VYRA_API_TOKEN` and HTTPS in front.
- Audience "templates" are starter ideas, not market research. Viewer statistics only count events recorded in this app.
- Posting to social channels is not implemented; export is manual (copy text, save images).
- Not tested: `npm run build`, Docker, PowerShell steps, Windows, Safari/Firefox. Only Chromium via Playwright.
- The Swagger UI loads its script from a CDN, so `/api/docs` needs internet.
