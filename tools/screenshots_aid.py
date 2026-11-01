"""Screenshots of the 援助与政治 tab: python tools/screenshots_aid.py [base_url] [date1] [date2]"""
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8765/"
D1 = sys.argv[2] if len(sys.argv) > 2 else "2022-04-26"
D2 = sys.argv[3] if len(sys.argv) > 3 else "2026-08-20"
OUT = Path(__file__).resolve().parent.parent / "screenshots"
OUT.mkdir(exist_ok=True)
errors = []


def goto_date(pg, d):
    pg.fill("#datePick", d)
    pg.dispatch_event("#datePick", "change")
    pg.wait_for_function(f"document.getElementById('aidDateLbl').textContent.includes('窗口') && "
                         f"document.getElementById('aidDateLbl').textContent.includes('{d}')", timeout=120000)
    pg.wait_for_timeout(2500)


with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1600, "height": 1100}, locale="zh-CN")
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    pg.goto(BASE, wait_until="networkidle")
    pg.wait_for_function("document.getElementById('sOcc').textContent.includes('km')", timeout=90000)
    pg.click('.tab[data-tab="tabAid"]')
    goto_date(pg, D1)
    pg.screenshot(path=str(OUT / "10_aid_panel_overview.png"))
    pg.evaluate("document.getElementById('panel').scrollTop = document.getElementById('aidList').offsetTop - 80")
    pg.wait_for_timeout(800)
    pg.screenshot(path=str(OUT / "10_aid_panel_list.png"))
    goto_date(pg, D2)
    pg.select_option("#aidWin", "7")
    pg.wait_for_function("document.getElementById('aidDateLbl').textContent.includes('窗口')", timeout=120000)
    pg.wait_for_timeout(2500)
    pg.click('.nf[data-c="political"]')
    pg.evaluate("document.getElementById('panel').scrollTop = document.getElementById('pnews').offsetTop - 420")
    pg.wait_for_timeout(800)
    pg.screenshot(path=str(OUT / "10_aid_panel_news.png"))
    b.close()
print("errors:", errors)
