"""Run VYRA against the LOCAL FAKE provider server (offline demo / UI tests).

Everything the AI returns here is canned by backend/tests/mock_providers.py. The Integrations page will say
"local test endpoint", never "verified". Usage: python scripts/mock_demo.py [--port 8000] [--data-dir DIR]
"""
import argparse, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "backend" / "tests")]
import mock_providers as mp  # noqa: E402
import uvicorn  # noqa: E402
from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--port", type=int, default=8000)
ap.add_argument("--data-dir", default=None)
a = ap.parse_args()
data = Path(a.data_dir or tempfile.mkdtemp(prefix="vyra-demo-"))
data.mkdir(parents=True, exist_ok=True)
srv = mp.MockServer().start()
settings = Settings(
    database_path=data / "vyra.db", media_dir=data / "media", frontend_dir=ROOT,
    amd_base_url=f"{srv.base}/amd/v1", amd_api_key=mp.AMD_KEY, amd_model="mock-amd-model",
    gemma_api_key=mp.GOOGLE_KEY, gemma_api_base=f"{srv.base}/google/v1beta",
    gemma_model="mock-gemma-text",
    cf_account_id=mp.CF_ACCOUNT, cf_api_token=mp.CF_TOKEN, cf_base_url=f"{srv.base}/cloudflare", cf_image_model="mock-cf-flux",
    pollinations_api_key=mp.POLLINATIONS_KEY, pollinations_base_url=f"{srv.base}/pollinations",
    pollinations_model="mock-flux",
    primary_llm="amd", fallback_llm="none", rate_limit_per_minute=1000,
    evolus_base_url=f"{srv.base}/evolus/v1")
print(f"VYRA mock demo on http://127.0.0.1:{a.port}  (data in {data})", flush=True)
uvicorn.run(create_app(settings), host="127.0.0.1", port=a.port, log_level="warning")
