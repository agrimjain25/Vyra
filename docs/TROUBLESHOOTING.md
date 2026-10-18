# Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `python` not found | Install Python 3.11+ with "Add to PATH"; reopen PowerShell. Try `py -3` instead. |
| Activate.ps1 blocked | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |
| `pip install` fails | Check network/proxy; upgrade pip: `python -m pip install --upgrade pip` |
| Server won't start: "configuration error" | The message names the bad `.env` value. Fix it in `backend\.env`. |
| Port 8000 in use | `$env:PORT=8001; python backend\run.py` |
| "AI service is not configured" | Fill the AMD (or Gemma) variables, restart the server (`.env` is read at start). |
| `auth_failed` | Wrong or expired key, or wrong base URL. Re-copy from the provider page. |
| `unsupported_model` / 404 | Model name or base URL wrong. For AMD check `AMD_MODEL` and that `AMD_BASE_URL` ends where `/chat/completions` is appended. |
| `quota_exhausted` / `rate_limited` | Credits used up / too many requests. Lower `LLM_CONCURRENCY`, wait, or set a fallback provider. |
| `timeout` | Raise `LLM_TIMEOUT_SECONDS` / `IMAGE_TIMEOUT_SECONDS`. |
| `invalid_output` | The model did not return valid structured JSON. Retry or try `AMD_JSON_MODE=none`. |
| `blocked` (images) | The image model's safety filter refused the prompt; edit the brief wording and retry. |
| `daily_cap` | `IMAGE_MAX_PER_DAY` reached; raise it deliberately. |
| Image slot shows an error | Click Retry; the exact reason is shown. |
| 401 `unauthorized` in the browser | `VYRA_API_TOKEN` is set: enter it in Settings → Access token. |
| 429 from VYRA | Local rate limit; wait a minute or raise `RATE_LIMIT_PER_MINUTE`. |
| Blank page | Open http://127.0.0.1:8000 (not the file directly); check the PowerShell window for errors. |
| Start over | Stop the server, delete `backend\data` and `backend\media`. |

Logs are JSON lines in the PowerShell window; keys are redacted.
