"""Start the mock demo, create+generate a campaign through the API, restart the process, verify data survived."""
import json, subprocess, sys, time, urllib.request, tempfile, os
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PORT = 8131; DATA = tempfile.mkdtemp(prefix="vyra-persist-")
def start():
    p = subprocess.Popen([sys.executable, f"{ROOT}/scripts/mock_demo.py", "--port", str(PORT), "--data-dir", DATA],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        try: urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/health", timeout=1); return p
        except Exception: time.sleep(0.3)
    raise SystemExit("server did not start")
def call(m, path, body=None):
    r = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", method=m, data=json.dumps(body).encode() if body is not None else None,
                               headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(r, timeout=30))
p = start()
c = call("POST", "/api/campaigns", {"name": "P", "brand": "B", "product": "Shoe", "description": "A shoe.", "objective": "Product launch",
    "tone": "Confident", "language": "English", "facts": ["Light"], "avoid": [], "audiences": [
    {"name": "A one", "needs": "comfort", "interests": "run", "angle": "Performance"}, {"name": "B two", "needs": "grip", "interests": "hike", "angle": "Style"}]})
cid = c["id"]; call("POST", f"/api/campaigns/{cid}/generate", {})
for _ in range(60):
    d = call("GET", f"/api/campaigns/{cid}")
    if all(e["status"] == "ready" for e in d["experiences"]) and d["experiences"] and not d.get("active_jobs"): break
    time.sleep(0.5)
before = [e["headline"] for e in d["experiences"]]
p.terminate(); p.wait(); p = start()
after = [e["headline"] for e in call("GET", f"/api/campaigns/{cid}")["experiences"]]
p.terminate(); p.wait()
ok = before == after and len(after) == 2
print(("PASS" if ok else "FAIL"), "campaign + generated content survive a server restart", after)
sys.exit(0 if ok else 1)
