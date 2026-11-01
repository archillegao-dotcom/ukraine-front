"""Public RSS headlines."""
import calendar
from datetime import datetime, timedelta, timezone

import feedparser

from . import net
from .config import NEWS_FEEDS, NEWS_KEYWORDS


def headlines(now_utc, log, per_feed=4, hours=36):
    cutoff = now_utc - timedelta(hours=hours)
    out = []
    for name, url, need_kw in NEWS_FEEDS:
        try:
            feed = feedparser.parse(net.get(url, timeout=30).content)
            items = []
            for e in feed.entries:
                t = e.get("published_parsed") or e.get("updated_parsed")
                ts = datetime.fromtimestamp(calendar.timegm(t), timezone.utc) if t else None
                if ts and ts < cutoff:
                    continue
                text = (e.get("title", "") + " " + e.get("summary", "")).lower()
                if need_kw and not any(k in text for k in NEWS_KEYWORDS):
                    continue
                items.append({"source": name, "title": e.get("title", "").strip(),
                              "url": e.get("link", ""), "ts": ts})
            items.sort(key=lambda x: x["ts"] or now_utc, reverse=True)
            out += items[:per_feed]
            log.ok(f"新闻 RSS: {name}", f"{len(items)} 条相关（{hours}h 内），取 {min(per_feed, len(items))} 条")
        except Exception as e:  # noqa: BLE001
            log.warn(f"新闻 RSS: {name}", f"失败：{e}")
    return out


# ---------------------------------------------------------------- web-app helper
import json as _json  # noqa: E402
import time as _time  # noqa: E402

from .config import CACHE  # noqa: E402


class _Quiet:
    def __init__(self):
        self.items = []

    def ok(self, s, m): self.items.append(("OK", s, m))
    def warn(self, s, m): self.items.append(("WARN", s, m))
    def fail(self, s, m): self.items.append(("FAIL", s, m))


def for_date(day, today, force=False):
    """Headlines for a given day. Live RSS for today/yesterday; Google News date search otherwise."""
    cdir = CACHE / "news"
    cdir.mkdir(parents=True, exist_ok=True)
    cf = cdir / f"{day}.json"
    fresh = day < today - timedelta(days=1) or (cf.exists() and _time.time() - cf.stat().st_mtime < 1800)
    if cf.exists() and fresh and not force:
        return _json.loads(cf.read_text(encoding="utf-8"))
    log = _Quiet()
    if day >= today - timedelta(days=1):
        end = datetime.combine(day + timedelta(days=1), datetime.min.time(), timezone.utc)
        items = headlines(min(datetime.now(timezone.utc), end), log, per_feed=5)
    else:
        items = []
        q = (f"Ukraine+Russia+war+after:{day - timedelta(days=1)}+before:{day + timedelta(days=1)}")
        url = f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
        try:
            feed = feedparser.parse(net.get(url, timeout=30).content)
            for e in feed.entries:
                text = e.get("title", "").lower()
                if not any(k in text for k in NEWS_KEYWORDS):
                    continue
                t = e.get("published_parsed")
                ts = datetime.fromtimestamp(calendar.timegm(t), timezone.utc) if t else None
                items.append({"source": "Google News 存档搜索", "title": e.get("title", ""),
                              "url": e.get("link", ""), "ts": ts})
                if len(items) >= 12:
                    break
            log.ok("Google News 存档搜索", f"{len(items)} 条")
        except Exception as e:  # noqa: BLE001
            log.warn("Google News 存档搜索", f"失败：{e}")
    res = {"items": [{**i, "ts": i["ts"].isoformat() if i["ts"] else None} for i in items],
           "sources": [{"level": a, "source": b, "msg": c} for a, b, c in log.items]}
    if items:
        cf.write_text(_json.dumps(res, ensure_ascii=False), encoding="utf-8")
    return res
