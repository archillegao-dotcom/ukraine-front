"""Take UI screenshots of the running app (python tools/screenshots.py [base_url])."""
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8765/"
OUT = Path(__file__).resolve().parent.parent / "screenshots"
OUT.mkdir(exist_ok=True)
errors = []
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1600, "height": 1000}, locale="zh-CN")
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(BASE, wait_until="networkidle")
    pg.wait_for_function("document.getElementById('sOcc').textContent.includes('km')", timeout=60000)
    pg.wait_for_timeout(2500)
    pg.screenshot(path=str(OUT / "01_overview_latest.png"))
    # 30-day comparison
    pg.select_option("#compare", "30")
    pg.wait_for_function("document.getElementById('compareInfo').textContent.includes('→')", timeout=60000)
    pg.wait_for_timeout(2500)
    pg.screenshot(path=str(OUT / "02_compare_30d.png"))
    # zoom to hottest sector and open popup on the largest change
    pg.click("#sectors li >> nth=0")
    pg.wait_for_timeout(3000)
    pg.evaluate("""() => { let best=null; chgLayer.eachLayer(l => { if(!best || l.feature.properties.area_km2 > best.feature.properties.area_km2) best=l; });
                         if (best) best.openPopup(best.getBounds().getCenter()); }""")
    pg.wait_for_timeout(2000)
    pg.screenshot(path=str(OUT / "03_sector_zoom_popup.png"))
    # pick an earlier date via the date picker and 7-day comparison
    pg.select_option("#compare", "7")
    pg.fill("#datePick", "2026-10-14")
    pg.dispatch_event("#datePick", "change")
    pg.wait_for_function("document.getElementById('snapInfo').textContent.includes('2026-10-14')", timeout=60000)
    pg.wait_for_timeout(1500)
    pg.evaluate("map.setView([49.0, 37.6], 9)")
    pg.wait_for_timeout(3000)
    pg.screenshot(path=str(OUT / "04_date_2026-10-14_7d.png"))
    # side panel full content (ISW, news, sources)
    pg.evaluate("document.getElementById('panel').scrollTop = 900")
    pg.wait_for_timeout(1500)
    pg.screenshot(path=str(OUT / "05_panel_timeline_isw.png"))
    pg.evaluate("document.getElementById('panel').scrollTop = 99999")
    pg.wait_for_timeout(800)
    pg.screenshot(path=str(OUT / "06_panel_news_sources.png"))
    # ISW era (before DeepState history) + full-war chart
    pg.evaluate("document.getElementById('panel').scrollTop = 0")
    pg.select_option("#compare", "7")
    pg.fill("#datePick", "2022-03-10")
    pg.dispatch_event("#datePick", "change")
    pg.wait_for_function("document.getElementById('snapInfo').textContent.includes('ISW')", timeout=60000)
    pg.wait_for_function("document.getElementById('compareInfo').textContent.includes('→')", timeout=60000)
    pg.evaluate("map.setView([49.2, 33.5], 6)")
    pg.wait_for_timeout(3500)
    pg.screenshot(path=str(OUT / "08_isw_era_2022-03-10.png"))
    pg.fill("#datePick", "2022-11-12")
    pg.dispatch_event("#datePick", "change")
    pg.select_option("#compare", "30")
    pg.wait_for_function("document.getElementById('snapInfo').textContent.includes('DeepState')", timeout=60000)
    pg.wait_for_timeout(1500)
    pg.evaluate("map.setView([47.3, 34.0], 7)")
    pg.wait_for_timeout(3500)
    pg.evaluate("document.getElementById('panel').scrollTop = 560")
    pg.wait_for_timeout(1000)
    pg.screenshot(path=str(OUT / "09_full_war_chart_kherson_2022-11.png"))
    b.close()
print("page errors:", errors or "none")
for f in sorted(OUT.glob("*.png")):
    print(f)
