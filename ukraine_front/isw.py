"""ISW Russian Offensive Campaign Assessment (via the CTP co-publication)."""
import re
from datetime import datetime

import feedparser
from bs4 import BeautifulSoup

from . import net
from .config import CTP_BASE, CTP_LIST, GNEWS_ISW

SLUG = re.compile(r'href="(/analysis/russian-offensive-campaign-assessment-'
                  r'([a-z]+)-(\d{1,2})-(\d{4})(?:-[0-9a-f]+)?)"')


def _list_reports():
    html = net.get(CTP_LIST).text
    out = {}
    for path, mon, d, y in SLUG.findall(html):
        try:
            dt = datetime.strptime(f"{mon} {d} {y}", "%B %d %Y").date()
        except ValueError:
            continue
        out.setdefault(dt, CTP_BASE + path)
    return out


def _parse(url):
    soup = BeautifulSoup(net.get(url).text, "lxml")
    title = soup.title.get_text(strip=True).split("|")[0].strip() if soup.title else ""
    takeaways = []
    for tag in soup.find_all(["p", "h2", "h3", "strong"]):
        if tag.get_text(strip=True).rstrip(":").lower() == "key takeaways":
            ul = tag.find_next("ul")
            if ul:
                takeaways = [re.sub(r"\[\d+\]", "", li.get_text(" ", strip=True)).strip()
                             for li in ul.find_all("li")]
            break
    asof = ""
    m = soup.find(string=re.compile(r"Assessment as of"))
    if m:
        asof = m.strip()
    return title, takeaways, asof


def latest(report_day, log, max_back=3):
    try:
        reports = _list_reports()
        days = sorted([d for d in reports if d <= report_day], reverse=True)
        if not days:
            raise RuntimeError("no ROCA report found on criticalthreats.org listing")
        res = []
        for d in days[:2]:  # the latest and the one before, for context
            if (report_day - d).days > max_back and res:
                break
            title, tk, asof = _parse(reports[d])
            res.append({"date": d, "title": title, "url": reports[d], "takeaways": tk,
                        "asof": asof})
        log.ok("ISW 每日评估 (经 criticalthreats.org 转载)",
               f"{res[0]['title']}：{len(res[0]['takeaways'])} 条要点")
        return res
    except Exception as e:  # noqa: BLE001
        log.warn("ISW 每日评估 (criticalthreats.org)", f"失败：{e}；尝试 Google News 标题")
    try:
        feed = feedparser.parse(net.get(GNEWS_ISW).content)
        items = [{"date": None, "title": en.title, "url": en.link, "takeaways": [], "asof": ""}
                 for en in feed.entries[:3]]
        if items:
            log.ok("ISW 标题 (Google News RSS)", f"{len(items)} 条，仅标题无要点")
        return items
    except Exception as e:  # noqa: BLE001
        log.fail("ISW (Google News RSS)", str(e))
        return []


# ---------------------------------------------------------------- web-app helpers
import json as _json  # noqa: E402
import time as _time  # noqa: E402
from datetime import timedelta as _td  # noqa: E402
from urllib.parse import quote_plus as _q  # noqa: E402

from .config import CACHE  # noqa: E402

_list_cache = {"t": 0, "v": {}}


def _listing(force=False):
    if force or _time.time() - _list_cache["t"] > 1800:
        _list_cache["v"] = _list_reports()
        _list_cache["t"] = _time.time()
    return _list_cache["v"]


def _slug_url(d):
    return f"{CTP_BASE}/analysis/russian-offensive-campaign-assessment-{d.strftime('%B').lower()}-{d.day}-{d.year}"


_blocked_until = 0.0   # back off after HTTP 403/429 (Cloudflare challenge / rate limit)


def for_date(day, today, force=False):
    """ISW report for `day` (or up to 2 days earlier). Cached on disk; a good cached result is
    never replaced by a worse fallback, and the site is not hammered after a 403/429.
    Returns dict(status, items=[...], note)."""
    global _blocked_until
    cdir = CACHE / "isw"
    cdir.mkdir(parents=True, exist_ok=True)
    cf = cdir / f"{day}.json"
    cached = _json.loads(cf.read_text(encoding="utf-8")) if cf.exists() else None
    fresh = cached and (day < today - _td(days=1) or _time.time() - cf.stat().st_mtime < 1800)
    if cached and fresh and not force:
        return cached
    if cached and cached.get("status") == "ok" and day < today - _td(days=1):
        return cached                       # past reports do not change
    if _time.time() < _blocked_until and cached:
        return {**cached, "note": (cached.get("note") or "") + "（来源暂时限制访问，显示缓存）"}
    errors = []
    listing = {}
    try:
        listing = _listing(force)
    except Exception as e:  # noqa: BLE001
        errors.append(f"criticalthreats 列表失败：{e}")
        if " 403 " in f" {e} " or "429" in str(e):
            _blocked_until = _time.time() + 3600
    # nothing newer published since the cached report -> keep it (saves requests)
    if cached and cached.get("status") == "ok" and listing:
        cdate = cached["items"][0].get("date")
        newest = max((d for d in listing if d <= day), default=None)
        if newest is not None and cdate and newest.isoformat() <= cdate:
            cf.touch()
            return cached
    res = None
    for back in range(0, 3):
        if _time.time() < _blocked_until:
            break
        d = day - _td(days=back)
        for url in [u for u in (listing.get(d), _slug_url(d)) if u]:
            try:
                title, tk, asof = _parse(url)
                res = {"status": "ok", "source": "criticalthreats.org（ISW 同步发布）",
                       "items": [{"date": d.isoformat(), "title": title, "url": url,
                                  "takeaways": tk, "asof": asof}],
                       "note": "" if back == 0 else f"{day} 无报告，显示 {d} 的报告"}
                break
            except Exception as e:  # noqa: BLE001
                errors.append(f"{url}: {e}")
                if "HTTP 403" in str(e) or "HTTP 429" in str(e):
                    _blocked_until = _time.time() + 3600
                    break
        if res:
            break
    if res is None and cached and cached.get("status") == "ok":
        return {**cached, "note": (cached.get("note") or "") + "（来源暂不可用，显示上次成功获取的内容）"}
    if res is None:  # fall back to a cached full report from the previous 1-3 days
        for back in range(1, 4):
            pf = cdir / f"{day - _td(days=back)}.json"
            if pf.exists():
                prev = _json.loads(pf.read_text(encoding="utf-8"))
                if prev.get("status") == "ok":
                    return {**prev, "note": f"criticalthreats.org 暂不可用，显示已缓存的 {prev['items'][0]['date']} 报告"}
    if res is None:
        try:
            q = (f"%22Russian+Offensive+Campaign+Assessment%22+after:{day - _td(days=2)}"
                 f"+before:{day + _td(days=1)}")
            feed = feedparser.parse(net.get(f"https://news.google.com/rss/search?q={q}"
                                            "&hl=en-US&gl=US&ceid=US:en").content)
            items = [{"date": None, "title": en.title, "url": en.link, "takeaways": [], "asof": ""}
                     for en in feed.entries[:3]]
            res = {"status": "partial" if items else "fail", "source": "Google News RSS（仅标题）",
                   "items": items, "note": "criticalthreats.org 暂不可用：" + "；".join(errors[-1:])}
        except Exception as e:  # noqa: BLE001
            res = {"status": "fail", "source": "", "items": [], "note": "；".join(errors[-2:] + [str(e)])}
    if res["status"] == "ok" or (res["status"] == "partial" and not cached):
        cf.write_text(_json.dumps(res, ensure_ascii=False), encoding="utf-8")
    return res
