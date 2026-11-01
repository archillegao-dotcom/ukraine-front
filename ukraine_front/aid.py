"""Aid to Ukraine (Kiel Institute Ukraine Support Tracker) + aid/political news index.

Tables (shipped, compact):
  data/aid/aid_events.csv.gz   one row per Kiel sub-activity (allocation) or commitment
  data/aid/aid_meta.json       release name, coverage end, download time, source URL
  data/aid/news_events.csv.gz  date,title,outlet,url,category,cats,query,auto_classified
Raw caches (not shipped): data/aid_raw/*.xlsx, data/cache/polnews/*.json
"""
import calendar
import csv
import gzip
import io
import json
import re
import threading
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote_plus

from . import net
from .config import CACHE, DATA, TZ

AID_DIR = DATA / "aid"
RAW_DIR = DATA / "aid_raw"
AID_CSV = AID_DIR / "aid_events.csv.gz"
AID_META = AID_DIR / "aid_meta.json"
NEWS_CSV = AID_DIR / "news_events.csv.gz"
NEWS_RAW = CACHE / "polnews"
KIEL_PAGE = "https://www.kielinstitut.de/research/topics/war-against-ukraine/ukraine-support-tracker/"
KIEL_BASE = "https://www.kielinstitut.de"
KIEL_SHEET = "Bilateral Assistance, MAIN DATA"
WAR_START = date(2022, 2, 24)
AID_FIELDS = ["date", "date_note", "donor", "category", "aid_type", "measure", "value_eur", "value_basis",
              "item", "item_numb", "description", "source_url", "source_name", "activity_id", "sub_activity_id"]
NEWS_FIELDS = ["date", "title", "outlet", "url", "category", "cats", "query", "auto_classified"]
CAT_MAP = {"Military": "military", "Financial": "financial", "Humanitarian": "humanitarian"}
_lock = threading.Lock()

# ------------------------------------------------------------------ Kiel ----------------------------


def _parse_kiel_date(v):
    """Returns (date|None, note). Kiel sometimes gives ranges such as 'until 12/31/2022'."""
    if isinstance(v, datetime):
        return v.date(), ""
    if isinstance(v, date):
        return v, ""
    s = str(v or "").strip()
    if not s or s == ".":
        return None, "无日期"
    ds = re.findall(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if ds:
        m, d, y = map(int, ds[-1])
        try:
            return date(y, m, d), f"区间：{s}（按区间末日归档）"
        except ValueError:
            pass
    try:
        return date.fromisoformat(s[:10]), ""
    except ValueError:
        return None, f"日期无法解析：{s}"


def _num(x):
    try:
        f = float(x)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def build_aid_table(xlsx, release, url, log=None):
    import pandas as pd
    df = pd.read_excel(xlsx, sheet_name=KIEL_SHEET, engine="openpyxl", usecols=list(range(0, 57)))
    rows = []
    for r in df.itertuples(index=False):
        rd = r._asdict()
        if rd.get("donor") in (None, "donor") or str(rd.get("donor")) == "nan":
            continue
        d, note = _parse_kiel_date(rd.get("announcement_date"))
        if rd.get("alloc_no_date") == 1 and not note:
            note = "Kiel 标注：无确切日期（按月/估计）"
        measure = str(rd.get("measure") or "")
        val = _num(rd.get("tot_sub_activity_value_EUR")) if measure == "Allocation" else \
            _num(rd.get("tot_activity_value_EUR"))
        basis = str(rd.get("value_source_activity_type") or "")
        if basis.startswith("Value is not given"):
            basis_cn = "来源未披露金额" + ("（Kiel 按单价估算）" if val else "")
        elif "calculated" in basis:
            basis_cn = "Kiel 按装备单价估算"
        else:
            basis_cn = "来源给出金额"
        src = str(rd.get("source_of_aid_1") or "")
        src = src if src.startswith("http") else ""
        item = str(rd.get("item") or "")
        rows.append({
            "date": d.isoformat() if d else "", "date_note": note, "donor": rd["donor"],
            "category": CAT_MAP.get(rd.get("aid_type_general"), "other"),
            "aid_type": str(rd.get("aid_type_specific") or "").strip(), "measure": measure,
            "value_eur": "" if val is None else round(val),
            "value_basis": basis_cn, "item": "" if item in (".", "nan") else item,
            "item_numb": "" if str(rd.get("item_numb")) in (".", "nan") else rd.get("item_numb"),
            "description": re.sub(r"\s+", " ", str(rd.get("explanation") or "")).strip(),
            "source_url": src, "source_name": f"Kiel Institute Ukraine Support Tracker ({release})",
            "activity_id": rd.get("activity_id"), "sub_activity_id": rd.get("sub_activity_id")})
    rows.sort(key=lambda x: (x["date"] or "9999", x["donor"]))
    AID_DIR.mkdir(parents=True, exist_ok=True)
    tmp = AID_CSV.with_suffix(".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, AID_FIELDS)
        w.writeheader()
        w.writerows(rows)
    tmp.replace(AID_CSV)
    # coverage line from the ReadMe sheet ("covering January 24, 2022 to August 31, 2026")
    cover = ""
    try:
        import openpyxl
        wb = openpyxl.load_workbook(xlsx, read_only=True, data_only=True)
        for row in wb["ReadMe"].iter_rows(max_row=3, values_only=True):
            cover = next((str(c) for c in row if c and "release" in str(c).lower()), "") or cover
            if cover:
                break
        wb.close()
    except Exception:  # noqa: BLE001
        pass
    meta = {"release": release, "xlsx_url": url, "page": KIEL_PAGE, "readme_title": cover,
            "coverage_end": _coverage_end(cover, rows), "rows": len(rows),
            "downloaded_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "checked_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    AID_META.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    if log:
        log.ok("Kiel 援助数据", f"{release}：{len(rows)} 条，覆盖至 {meta['coverage_end']}")
    _invalidate()
    return meta


def _coverage_end(cover, rows):
    m = re.search(r"to ([A-Z][a-z]+ \d{1,2}, \d{4})", cover or "")
    if m:
        try:
            return datetime.strptime(m.group(1), "%B %d, %Y").date().isoformat()
        except ValueError:
            pass
    return max(r["date"] for r in rows if r["date"]) if rows else ""


def read_meta():
    try:
        return json.loads(AID_META.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def check_kiel(log=None, min_hours=24, force=False):
    """Check the tracker page for a new XLSX release (at most once per `min_hours`)."""
    meta = read_meta()
    last = meta.get("checked_utc")
    if not force and last and AID_CSV.exists():
        if datetime.now(timezone.utc) - datetime.fromisoformat(last) < timedelta(hours=min_hours):
            return False
    html = net.get(KIEL_PAGE, timeout=40).text
    links = re.findall(r'href="([^"]+Ukraine_Support_Tracker[^"]*\.xlsx)"', html)
    if not links:
        raise RuntimeError("Kiel 页面未找到 XLSX 链接")
    url = links[0] if links[0].startswith("http") else KIEL_BASE + links[0]
    m = re.search(r"Release_(\d+)", url)
    release = f"Release {m.group(1)}" if m else url.rsplit("/", 1)[-1]
    if meta.get("xlsx_url") == url and AID_CSV.exists() and not force:
        meta["checked_utc"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        AID_META.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        if log:
            log.ok("Kiel 援助数据", f"无新版本（仍为 {release}）")
        return False
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    xp = RAW_DIR / (release.replace(" ", "_") + ".xlsx")
    if not xp.exists():
        r = net.get(url, timeout=300)
        xp.write_bytes(r.content)
    build_aid_table(xp, release, url, log)
    return True


# ------------------------------------------------------------------ news ----------------------------

NEWS_QUERIES = [
    ("aid", "Ukraine (aid OR weapons OR loan OR package OR humanitarian OR military)"),
    ("politics", "Ukraine sanctions OR Ukraine talks OR Ukraine ceasefire OR Ukraine NATO OR Ukraine summit"),
]
# keyword rules (lower-case, matched on headline); first category with most hits wins
RULES = {
    "military": ["weapon", "arms", "military aid", "ammunition", "ammo", "shells", "artillery", "himars",
                 "patriot", "air defen", "air-defen", "tank", "leopard", "abrams", "f-16", "jets", "missile",
                 "atacms", "storm shadow", "taurus", "drone", "howitzer", "security assistance", "pentagon",
                 "drawdown", "usai", "ramstein", "purl", "defense ministry", "defence", "military package",
                 "armored", "armoured", "munitions", "rifles", "nasams", "iris-t", "arm ukraine", "arming",
                 "helicopter", "military assistance", "military support", "deliveries", "anti-tank", "javelin",
                 "stinger", "warplane", "fighter jet", "armaments", "lend-lease", "lend lease"],
    "financial": ["loan", "budget support", "imf", "world bank", "billion", "million euro", "financial aid",
                  "macro-financial", "ukraine facility", "frozen asset", "russian assets", "reparations loan",
                  "reconstruction", "debt", "grant", "funding", "economy", "economic", "bonds", "tariff",
                  "eib", "ebrd", "investment", "minerals deal", "mineral", "$", "€", "£", "bn ", "bln", "aid package",
                  "financial support", "spending", "revenue", "central bank", "hryvnia", "budget", "fund "],
    "humanitarian": ["humanitarian", "refugee", "civilian", "evacuat", "food", "grain", "medical",
                     "hospital", "unhcr", "red cross", "unicef", "winter", "generator", "energy aid",
                     "shelter", "children", "displaced", "relief"],
    "political": ["sanction", "talks", "negotiat", "summit", "ceasefire", "cease-fire", "peace", "nato",
                  "european union", " eu ", "eu's", "g7", "un security council", "general assembly",
                  "membership", "accession", "election", "putin", "zelensky", "zelenskyy", "biden", "trump",
                  "kremlin", "diplomat", "envoy", "meeting", "visit", "treaty", "agreement", "deal",
                  "embassy", "parliament", "congress", "senate", "vote", "icc", "tribunal", "foreign minister",
                  "president", "prime minister", "chancellor", "macron", "scholz", "merz", "starmer", "erdogan",
                  "xi ", "lavrov", "blinken", "rubio", "witkoff", "white house", "security guarantee"],
}
CAT_ORDER = ["military", "financial", "humanitarian", "political"]
_last_req = [0.0]
_gnews_blocked_until = [0.0]


def classify(title):
    t = " " + title.lower() + " "
    score = {c: sum(1 for k in kws if k in t) for c, kws in RULES.items()}
    cats = [c for c in CAT_ORDER if score[c] > 0]
    if not cats:
        return "other", []
    best = max(cats, key=lambda c: (score[c], -CAT_ORDER.index(c)))
    return best, cats


def _gnews(q, after, before, min_gap=2.0):
    if time.time() < _gnews_blocked_until[0]:
        raise RuntimeError("Google News 暂时限流，稍后再试")
    wait = _last_req[0] + min_gap - time.time()
    if wait > 0:
        time.sleep(wait)
    url = ("https://news.google.com/rss/search?q=" + quote_plus(f"{q} after:{after} before:{before}")
           + "&hl=en-US&gl=US&ceid=US:en")
    try:
        r = net.get(url, timeout=30, retries=2)
    except Exception as e:
        if "429" in str(e) or "503" in str(e):
            _gnews_blocked_until[0] = time.time() + 1800
        raise
    finally:
        _last_req[0] = time.time()
    import feedparser
    out = []
    for e in feedparser.parse(r.content).entries:
        t = e.get("published_parsed")
        if not t:
            continue
        ts = datetime.fromtimestamp(calendar.timegm(t), timezone.utc)
        title = e.get("title", "").strip()
        outlet = (e.get("source") or {}).get("title", "") if isinstance(e.get("source"), dict) else ""
        if outlet and title.endswith(" - " + outlet):
            title = title[: -len(outlet) - 3].strip()
        out.append({"ts": ts.isoformat(), "title": title, "outlet": outlet, "url": e.get("link", "")})
    return out


def fetch_window(start, force=False):
    """Fetch the 2-day window [start, start+1] for all queries; cached as raw JSON per window."""
    NEWS_RAW.mkdir(parents=True, exist_ok=True)
    cf = NEWS_RAW / f"{start.isoformat()}.json"
    if cf.exists() and not force:
        return json.loads(cf.read_text(encoding="utf-8"))
    res = {"window_start": start.isoformat(), "fetched_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "items": []}
    for key, q in NEWS_QUERIES:
        for it in _gnews(q, start - timedelta(days=1), start + timedelta(days=2)):
            it["query"] = key
            res["items"].append(it)
    cf.write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
    return res


def window_start(d):
    """Windows are aligned on even day offsets from WAR_START."""
    return d - timedelta(days=(d - WAR_START).days % 2)


def _norm(item):
    ts = datetime.fromisoformat(item["ts"]).astimezone(TZ)
    cat, cats = classify(item["title"])
    return {"date": ts.date().isoformat(), "title": item["title"], "outlet": item["outlet"],
            "url": item["url"], "category": cat, "cats": "|".join(cats), "query": item["query"],
            "auto_classified": 1}


def build_news_index(per_day=24):
    """Merge all raw windows into the shipped compact index (keeps <= per_day items per date)."""
    by = defaultdict(dict)
    # keep the shipped/previous index (raw caches may only hold recent windows on user machines)
    for r in _read_csv_gz(NEWS_CSV):
        by[r["date"]].setdefault(re.sub(r"\W+", "", r["title"].lower())[:90], r)
    for p in sorted(NEWS_RAW.glob("*.json")):
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        for it in raw["items"]:
            n = _norm(it)
            if n["date"] < WAR_START.isoformat():
                continue
            k = re.sub(r"\W+", "", n["title"].lower())[:90]
            by[n["date"]].setdefault(k, n)
    rows = []
    for d in sorted(by):
        items = list(by[d].values())
        # balance: keep categorised headlines first, round-robin over categories
        buckets = defaultdict(list)
        for it in items:
            buckets[it["category"]].append(it)
        keep = []
        order = CAT_ORDER + ["other"]
        while len(keep) < per_day and any(buckets[c] for c in order):
            for c in order:
                if buckets[c] and len(keep) < per_day:
                    keep.append(buckets[c].pop(0))
        rows += sorted(keep, key=lambda x: CAT_ORDER.index(x["category"]) if x["category"] in CAT_ORDER else 9)
    AID_DIR.mkdir(parents=True, exist_ok=True)
    tmp = NEWS_CSV.with_suffix(".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, NEWS_FIELDS)
        w.writeheader()
        w.writerows(rows)
    tmp.replace(NEWS_CSV)
    _invalidate()
    return len(rows), len(by)


# ------------------------------------------------------------------ in-memory access ----------------
_mem = {}


def _invalidate():
    with _lock:
        _mem.clear()


def _read_csv_gz(p):
    if not p.exists():
        return []
    with gzip.open(p, "rt", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def aid_rows():
    with _lock:
        if "aid" not in _mem:
            rows = _read_csv_gz(AID_CSV)
            for r in rows:
                r["value_eur"] = float(r["value_eur"]) if r["value_eur"] else None
            _mem["aid"] = rows
        return _mem["aid"]


def news_rows():
    mt = NEWS_CSV.stat().st_mtime if NEWS_CSV.exists() else 0
    with _lock:
        if "news" not in _mem or _mem.get("news_mtime") != mt:
            _mem["news_mtime"] = mt
            idx = defaultdict(list)
            for r in _read_csv_gz(NEWS_CSV):
                idx[r["date"]].append(r)
            _mem["news"] = idx
        return _mem["news"]


def news_for_range(d0, d1, fetch_missing=True, max_fetch=4):
    """News between d0..d1 (inclusive). Missing windows are fetched on demand (rate-limited)."""
    idx = news_rows()
    fetched, errors = 0, []
    extra = defaultdict(dict)
    d = d0
    while d <= d1:
        if not idx.get(d.isoformat()) and d >= WAR_START:
            ws = window_start(d)
            cf = NEWS_RAW / f"{ws.isoformat()}.json"
            stale_today = d >= datetime.now(TZ).date() - timedelta(days=1)
            if fetch_missing and (not cf.exists() or stale_today) and fetched < max_fetch:
                try:
                    if not cf.exists() or time.time() - cf.stat().st_mtime > 3600:
                        fetch_window(ws, force=True)
                        fetched += 1
                except Exception as e:  # noqa: BLE001
                    errors.append(str(e))
            if cf.exists():
                for it in json.loads(cf.read_text(encoding="utf-8"))["items"]:
                    n = _norm(it)
                    if n["date"] == d.isoformat():
                        extra[n["date"]].setdefault(re.sub(r"\W+", "", n["title"].lower())[:90], n)
        d += timedelta(days=1)
    out = []
    d = d0
    while d <= d1:
        k = d.isoformat()
        out += idx.get(k) or list(extra.get(k, {}).values())[:30]
        d += timedelta(days=1)
    return out, {"fetched_windows": fetched, "errors": errors}


def refresh_recent_news(log=None, days=3, max_gap_windows=15):
    """Scheduled job: re-fetch the windows touching the last `days` days, fill gaps since the last
    indexed date (at most `max_gap_windows` windows per run), and merge into the index."""
    today = datetime.now(TZ).date()
    idx = news_rows()
    last = max((date.fromisoformat(k) for k in idx), default=today - timedelta(days=days))
    force = {window_start(today - timedelta(days=i)) for i in range(days)}
    gap, ws = [], window_start(min(last, today - timedelta(days=days)))
    while ws <= today:
        if ws not in force and not (NEWS_RAW / f"{ws}.json").exists():
            gap.append(ws)
        ws += timedelta(days=2)
    starts = sorted(force) + gap[:max_gap_windows]
    n = 0
    for ws in starts:
        try:
            raw = fetch_window(ws, force=ws in force)
            n += len(raw["items"])
        except Exception as e:  # noqa: BLE001
            if log:
                log.warn("援助/政治新闻 (Google News)", f"{ws} 获取失败：{e}")
            break
    rows, days_n = build_news_index()
    if log:
        log.ok("援助/政治新闻 (Google News)", f"窗口 {len(starts)} 个，{n} 条原始标题；索引共 {rows} 条 / {days_n} 天"
               + (f"；另有 {len(gap) - max_gap_windows} 个缺口窗口留待下次" if len(gap) > max_gap_windows else ""))
    return n


# ------------------------------------------------------------------ aggregates -----------------------

def cumulative_series():
    """Daily cumulative allocated EUR by category (Allocations only, dated rows)."""
    with _lock:
        if "cum" in _mem:
            return _mem["cum"]
    per = defaultdict(lambda: defaultdict(float))
    for r in aid_rows():
        if r["measure"] != "Allocation" or not r["value_eur"]:
            continue
        per[r["date"] or read_meta().get("coverage_end", "9999")][r["category"]] += r["value_eur"]
    out, run = [], defaultdict(float)
    for d in sorted(per):
        for c, v in per[d].items():
            run[c] += v
        out.append({"date": d, **{c: round(run[c] / 1e9, 4) for c in ("military", "financial", "humanitarian")},
                    "total": round(sum(run.values()) / 1e9, 4)})
    with _lock:
        _mem["cum"] = out
    return out


def summary_until(day, top=10):
    ds = day.isoformat()
    cov = read_meta().get("coverage_end", "")
    by_cat, by_donor = defaultdict(float), defaultdict(float)
    commit = defaultdict(float)
    # Kiel lists one row per item; the sub-activity value sits on one row only, the other item rows of
    # the same sub-activity are blank -> count "without value" per sub-activity, not per row.
    subs = {}
    for r in aid_rows():
        if (r["date"] or cov) > ds:
            continue
        if r["measure"] == "Commitment":
            commit[r["category"]] += r["value_eur"] or 0
            continue
        k = r["sub_activity_id"] or r["activity_id"]
        subs[k] = subs.get(k, False) or bool(r["value_eur"])
        if not r["value_eur"]:
            continue
        by_cat[r["category"]] += r["value_eur"]
        by_donor[r["donor"]] += r["value_eur"]
    n, n_noval = len(subs), sum(1 for v in subs.values() if not v)
    donors = sorted(by_donor.items(), key=lambda x: -x[1])[:top]
    return {"until": ds, "allocations": n, "allocations_without_value": n_noval,
            "by_category_eur": {k: round(v) for k, v in by_cat.items()},
            "total_eur": round(sum(by_cat.values())),
            "commitments_eur": {k: round(v) for k, v in commit.items()},
            "top_donors": [{"donor": d, "eur": round(v)} for d, v in donors]}


def events_between(d0, d1):
    """Kiel rows in [d0, d1] grouped per activity (one announcement/package = one record)."""
    a, b = d0.isoformat(), d1.isoformat()
    groups = {}
    for r in aid_rows():
        if not r["date"] or not (a <= r["date"] <= b):
            continue
        k = (r["activity_id"], r["measure"], r["date"])
        g = groups.get(k)
        if g is None:
            g = groups[k] = {"date": r["date"], "date_note": r["date_note"], "donor": r["donor"],
                             "category": r["category"], "aid_type": r["aid_type"], "measure": r["measure"],
                             "value_eur": None, "value_basis": set(), "items": [], "description": r["description"],
                             "source_url": r["source_url"], "source_name": r["source_name"],
                             "activity_id": r["activity_id"], "n_rows": 0}
        g["n_rows"] += 1
        if r["value_eur"]:
            g["value_eur"] = (g["value_eur"] or 0) + r["value_eur"]
        g["value_basis"].add(r["value_basis"])
        if r["item"]:
            g["items"].append(f"{r['item']} ×{r['item_numb']}" if r["item_numb"] else r["item"])
        if r["aid_type"] and r["aid_type"] not in g["aid_type"]:
            g["aid_type"] += " / " + r["aid_type"]
    out = []
    for g in groups.values():
        g["value_basis"] = "；".join(sorted(x for x in g["value_basis"] if x))
        g["items"] = g["items"][:25]
        g["value_eur"] = round(g["value_eur"]) if g["value_eur"] else None
        out.append(g)
    out.sort(key=lambda g: (g["date"], -(g["value_eur"] or 0)), reverse=True)
    return out
