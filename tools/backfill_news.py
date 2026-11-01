"""Backfill aid/political headlines (Google News RSS date search) for the whole war, politely.
2 queries per 2-day window, >= 2 s between requests, cached per window (resumable).
Usage: python tools/backfill_news.py [YYYY-MM-DD start] [YYYY-MM-DD end]"""
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ukraine_front import aid  # noqa: E402
from ukraine_front.config import TZ  # noqa: E402

start = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else aid.WAR_START
end = date.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else datetime.now(TZ).date()
ws = aid.window_start(start)
n = fails = 0
t0 = time.time()
while ws <= end:
    cf = aid.NEWS_RAW / f"{ws}.json"
    if not cf.exists():
        try:
            r = aid.fetch_window(ws)
            n += 1
            print(ws, len(r["items"]), f"{time.time()-t0:.0f}s", flush=True)
            fails = 0
        except Exception as e:  # noqa: BLE001
            fails += 1
            print(ws, "ERR", e, flush=True)
            time.sleep(min(600, 30 * fails))
            if fails >= 8:
                print("too many failures, stopping"); break
            continue
    ws += timedelta(days=2)
    if n and n % 50 == 0:
        aid.build_news_index()
print("index:", aid.build_news_index())
