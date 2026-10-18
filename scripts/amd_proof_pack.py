"""Write the AMD proof summary from the calls this server really recorded.

Run from the project root, after you generated at least one campaign with AMD configured:
    python scripts/amd_proof_pack.py

Output: evidence/amd_proof.md (readable, with a ready-to-paste statement) and evidence/amd_proof.json.
Nothing is invented. If no successful call to a non-local AMD endpoint was recorded, the file says so.
It does NOT prove the endpoint runs on AMD hardware: pair it with a screenshot of the AMD Token Factory dashboard.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
from app.config import Settings  # noqa: E402
from app.db import Database  # noqa: E402
from app.repo import Repo  # noqa: E402
from app.services import amd_proof  # noqa: E402


def main() -> int:
    s = Settings.from_env()
    if not s.database_path.is_file():
        print("No database found at", s.database_path, "- generate a campaign first.")
        return 2
    repo = Repo(Database(s.database_path))
    proof = amd_proof.summarize(repo, s)
    out_dir = ROOT / "evidence"
    out_dir.mkdir(exist_ok=True)
    (out_dir / "amd_proof.json").write_text(json.dumps(proof, indent=2), encoding="utf-8")
    (out_dir / "amd_proof.md").write_text(amd_proof.to_markdown(proof), encoding="utf-8")
    print(amd_proof.to_markdown(proof))
    print("Saved evidence/amd_proof.md and evidence/amd_proof.json")
    return 0 if proof["amd"]["real_calls"] else 1


sys.exit(main())
