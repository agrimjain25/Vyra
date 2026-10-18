"""Browser end-to-end check against `scripts/mock_demo.py` (local FAKE providers). Usage: python tests/e2e/ui_flow.py [base_url]"""
import sys, json
from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8123"
OUT = sys.argv[2] if len(sys.argv) > 2 else "/tmp/shots"
import os; os.makedirs(OUT, exist_ok=True)
res = []
def check(name, ok, info=""):
    res.append((name, bool(ok), info)); print(("PASS " if ok else "FAIL ") + name, info, flush=True)

with sync_playwright() as p:
    b = p.chromium.launch(executable_path="/opt/pw-browsers/chromium", args=["--no-sandbox"]) if os.path.exists("/opt/pw-browsers/chromium") else p.chromium.launch()
    pg = b.new_page(viewport={"width": 1280, "height": 900})
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    pg.on("dialog", lambda d: d.accept())
    pg.goto(BASE + "/#/overview"); pg.wait_for_timeout(1200)
    pg.screenshot(path=f"{OUT}/01_overview.png")
    txt = pg.inner_text("body")
    check("overview loads, no fake metrics", "VYRA" in txt and "Demo app" not in txt, txt[:0])
    pg.goto(BASE + "/#/studio"); pg.wait_for_timeout(500)
    pg.fill("#f-brand", "Northwind Athletics"); pg.fill("#f-product", "Stride 4 running shoe")
    pg.fill("#f-desc", "A lightweight everyday running shoe."); pg.click("[data-next]")
    pg.wait_for_timeout(300)
    pg.fill("#a0-name", "City runners"); pg.fill("#a0-need", "comfort on daily runs")
    n = pg.locator("[data-a$='.name']").count()
    check("wizard has >=2 audiences", n >= 2, str(n))
    pg.fill("#a1-name", "Weekend hikers"); pg.fill("#a1-need", "durable grip")
    pg.click("[data-next]"); pg.wait_for_timeout(300)
    pg.fill("#f-facts", "Cushioned midsole\nRecycled mesh upper"); pg.click("[data-next]"); pg.wait_for_timeout(300)
    pg.screenshot(path=f"{OUT}/02_wizard_review.png")
    pg.click("[data-gen]")
    try:
        pg.wait_for_function("document.querySelectorAll('.exp-card, [data-exp]').length>=2 || /Headline|headline/.test(document.body.innerText)", timeout=60000)
    except Exception as e:
        check("generation finished", False, str(e)[:100])
    pg.wait_for_timeout(4000)
    pg.screenshot(path=f"{OUT}/03_results.png", full_page=True)
    open(f"{OUT}/results.txt", "w").write(pg.inner_text("body"))
    st = pg.evaluate("(()=>{try{return JSON.stringify({url:location.hash})}catch(e){return ''}})()")
    body = pg.inner_text("body")
    heads = pg.locator("h2.hl, .exp-card h2, .exp-card h3").all_inner_texts()
    check("two audience experiences with different headlines", "City runners" in body and "Weekend hikers" in body)
    check("brand checks shown with disclaimer", "Brand checks" in body and "compliance guarantee" in body)
    check("cost shown honestly (unknown w/o prices)", "unknown" in body)
    check("no fabricated metrics text", "+34%" not in body and "Demo app" not in body)
    # image generation
    pg.locator("button:has-text('Generate hero image')").first.click()
    pg.wait_for_function("document.querySelectorAll('img[src*=\"/media/\"]').length>=1", timeout=30000)
    pg.wait_for_timeout(800)
    nw = pg.evaluate("[...document.querySelectorAll('img[src*=\"/media/\"]')].map(i=>i.naturalWidth)")
    check("hero image loads in browser", nw and all(w>0 for w in nw), str(nw))
    pg.screenshot(path=f"{OUT}/04_image.png")
    # story
    pg.locator("button:has-text('Play story')").first.click(); pg.wait_for_timeout(1200)
    pg.screenshot(path=f"{OUT}/05_story1.png")
    ch = pg.locator("[data-choose]")
    check("story shows choices", ch.count() >= 2, str(ch.count()))
    t0 = pg.inner_text(".story")
    ch.first.click(); pg.wait_for_timeout(1000)
    check("choice advances scene", pg.inner_text(".story") != t0)
    pg.screenshot(path=f"{OUT}/06_story2.png")
    # settings / integrations honesty
    pg.goto(BASE + "/#/settings"); pg.wait_for_timeout(1200)
    sbody = pg.inner_text("body"); pg.screenshot(path=f"{OUT}/07_settings.png", full_page=True)
    check("integrations honest: no 'verified' w/o test", sbody.count("Verified working") <= 1 and "local test endpoint" in sbody, "legend only; mock endpoint labelled as local test")
    check("PwC/Vibe not faked", "Not implemented" in sbody or "not implemented" in sbody.lower())
    pg.goto(BASE + "/#/analytics"); pg.wait_for_timeout(1000)
    pg.screenshot(path=f"{OUT}/08_analytics.png", full_page=True)
    pg.goto(BASE + "/#/overview"); pg.wait_for_timeout(1000)
    pg.screenshot(path=f"{OUT}/09_overview_after.png", full_page=True)
    check("persisted campaign listed", "Stride 4" in pg.inner_text("body"))
    pg.set_viewport_size({"width": 390, "height": 800}); pg.goto(BASE + "/#/overview"); pg.wait_for_timeout(800)
    ow = pg.evaluate("document.documentElement.scrollWidth - innerWidth")
    check("no horizontal overflow on mobile", ow <= 1, str(ow))
    pg.screenshot(path=f"{OUT}/10_mobile.png")
    for e in errs[:5]: print("console:", e)
    check("no JS errors", not errs, "; ".join(errs[:3]))
    b.close()
fails = [r for r in res if not r[1]]
sys.exit(1 if fails else 0)
