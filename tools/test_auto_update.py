"""E2E check: page follows new data pushed by the background scheduler; saves 07_auto_update.png."""
import sys, time
from pathlib import Path
from playwright.sync_api import sync_playwright
BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8765/"
OUT = Path(__file__).resolve().parent.parent / "screenshots"
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1600, "height": 1000}, locale="zh-CN")
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(BASE, wait_until="networkidle")
    pg.wait_for_function("document.getElementById('sOcc').textContent.includes('km')", timeout=60000)
    before = pg.evaluate("curDate()")
    pg.evaluate("map.setView([48.55, 37.9], 10)")
    view0 = pg.evaluate("[map.getCenter().lat.toFixed(3), map.getCenter().lng.toFixed(3), map.getZoom()]")
    print("before:", before, "view:", view0, "status:", pg.inner_text("#autoStatus").replace("\n", " | "))
    t = time.time()
    pg.wait_for_function(f"curDate() !== '{before}'", timeout=240000)
    pg.wait_for_function("document.getElementById('toast').style.display === 'block'", timeout=30000)
    pg.wait_for_timeout(2500)
    after = pg.evaluate("curDate()")
    view1 = pg.evaluate("[map.getCenter().lat.toFixed(3), map.getCenter().lng.toFixed(3), map.getZoom()]")
    print(f"after {time.time() - t:.0f}s:", after, "view:", view1, "kept view:", view0 == view1)
    print("toast:", pg.inner_text("#toast"))
    print("status:", pg.inner_text("#autoStatus").replace("\n", " | "))
    print("snap:", pg.inner_text("#snapInfo"))
    pg.screenshot(path=str(OUT / "07_auto_update.png"))
    print("page errors:", errs or "none")
    b.close()
