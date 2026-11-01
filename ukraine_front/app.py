"""Interactive local web app: python -m ukraine_front.app [--port 8765] [--no-browser]"""
import argparse
import hashlib
import logging
from logging.handlers import RotatingFileHandler
from contextlib import asynccontextmanager
import json
import math
import os
import sys
import threading
import time
import traceback
import webbrowser
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

import shapely
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from pyproj import Transformer
from shapely.geometry import Point, mapping
from shapely.ops import transform

from . import aid, core, deepstate, geo, isw, news
from .config import CACHE, DATA, LOCAL_CRS, TZ

WEB = Path(__file__).parent / "web"
PREFETCH_DAYS = int(os.environ.get("UF_PREFETCH_DAYS", "31"))
_to_local = Transformer.from_crs("EPSG:4326", LOCAL_CRS, always_xy=True).transform



@asynccontextmanager
async def _lifespan(_app):
    _setup_logging()
    _load_cached_history()
    _load_sched_state()
    if not SCHED["data_version"]:
        SCHED["data_version"] = "init"
    log_.info("自动更新间隔：%s 分钟（来源：%s），下次：%s", SCHED["interval_min"], SCHED["interval_source"],
              _paris_hm(SCHED["next_run"], True))
    threading.Thread(target=_scheduler_loop, daemon=True, name="uf-scheduler").start()
    _sched_wake.set()  # stale data is refreshed right away, in the background
    yield


app = FastAPI(title="俄乌前线 OSINT 追踪器", lifespan=_lifespan)


class Log:
    def __init__(self):
        self.items = []

    def ok(self, s, m): self.items.append({"level": "OK", "source": s, "msg": m})
    def warn(self, s, m): self.items.append({"level": "WARN", "source": s, "msg": m})
    def fail(self, s, m): self.items.append({"level": "FAIL", "source": s, "msg": m})


STATE = {"history": [], "by_id": {}, "refresh": {"running": False, "step": "未开始", "done": 0, "total": 0,
                                                   "started": None, "finished": None, "log": [], "trigger": None}}
HISTORY_START = date.fromisoformat(os.environ.get("UF_HISTORY_START", "2022-02-24"))
SETTINGS_FILE = DATA / "settings.json"
SCHED_FILE = CACHE / "scheduler_state.json"
_run_lock = threading.Lock()
_sched_wake = threading.Event()
log_ = logging.getLogger("ukraine_front")


def _setup_logging():
    if log_.handlers:
        return
    (DATA / "logs").mkdir(parents=True, exist_ok=True)
    fh = RotatingFileHandler(DATA / "logs" / "app.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    sh = logging.StreamHandler()
    sh.setFormatter(logging.Formatter("[%(asctime)s] %(message)s", "%H:%M:%S"))
    log_.addHandler(fh)
    log_.addHandler(sh)
    log_.setLevel(logging.INFO)


def _initial_interval():
    env = os.environ.get("UF_REFRESH_MINUTES")
    if env:
        return max(0, int(float(env))), "env"
    try:
        return int(json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))["refresh_minutes"]), "settings"
    except Exception:  # noqa: BLE001
        return 60, "default"


SCHED = {"interval_min": 60, "interval_source": "default", "next_run": None, "last_run_end": None,
         "last_new_data": None, "data_version": "", "runs": 0}


def _load_sched_state():
    SCHED["interval_min"], SCHED["interval_source"] = _initial_interval()
    try:
        st = json.loads(SCHED_FILE.read_text(encoding="utf-8"))
        for k in ("last_run_end", "last_new_data"):
            SCHED[k] = datetime.fromisoformat(st[k]) if st.get(k) else None
        SCHED["data_version"] = st.get("data_version", "")
    except Exception:  # noqa: BLE001
        pass
    now = datetime.now(timezone.utc)
    iv = SCHED["interval_min"]
    last = SCHED["last_run_end"]
    if iv <= 0:
        SCHED["next_run"] = None if last else now   # still refresh once if never refreshed
    elif last is None or now - last >= timedelta(minutes=iv):
        SCHED["next_run"] = now                       # stale -> refresh right away
    else:
        SCHED["next_run"] = last + timedelta(minutes=iv)


def _save_sched_state():
    SCHED_FILE.parent.mkdir(parents=True, exist_ok=True)
    SCHED_FILE.write_text(json.dumps({
        "last_run_end": SCHED["last_run_end"].isoformat() if SCHED["last_run_end"] else None,
        "last_new_data": SCHED["last_new_data"].isoformat() if SCHED["last_new_data"] else None,
        "data_version": SCHED["data_version"]}, ensure_ascii=False), encoding="utf-8")


def today():
    return datetime.now(TZ).date()


def _set_history(h):
    STATE["history"] = h
    STATE["by_id"] = {x["id"]: x for x in h}


def _load_cached_history():
    cf = CACHE / "deepstate_history.json"
    data = []
    if cf.exists():
        data = json.loads(cf.read_text(encoding="utf-8"))
        for d in data:
            d["_ts"] = deepstate.parse_ts(d["createdAt"])
        data.sort(key=lambda d: d["_ts"])
    _set_history(core.merge_history(data))


def _data_version():
    """Fingerprint of everything the UI shows; changes => clients reload."""
    h = STATE["history"]
    m = core.read_metrics()
    tail = [(k, m[k].get("snapshot_id"), m[k].get("occupied_km2")) for k in sorted(m)[-3:]]
    parts = [h[-1]["id"] if h else None, len(m), tail]
    try:
        i = isw.for_date(today(), today())
        parts.append([(x.get("url"), len(x.get("takeaways") or [])) for x in i.get("items", [])])
    except Exception:  # noqa: BLE001
        pass
    try:
        parts.append([x.get("url") for x in news.for_date(today(), today()).get("items", [])][:8])
    except Exception:  # noqa: BLE001
        pass
    am = aid.read_meta()
    parts.append([am.get("release"), am.get("rows"),
                  aid.NEWS_CSV.stat().st_size if aid.NEWS_CSV.exists() else 0])
    return hashlib.sha1(json.dumps(parts, default=str).encode()).hexdigest()[:12]


def refresh_job(trigger="manual"):
    """Fetch new DeepState snapshots, ISW and news. Never runs twice concurrently."""
    if not _run_lock.acquire(blocking=False):
        log_.info("刷新已在进行中，跳过本次触发（%s）", trigger)
        return False
    r = STATE["refresh"]
    t0 = datetime.now(timezone.utc)
    r.update(running=True, step="获取 DeepState 快照列表", done=0, total=0, trigger=trigger,
             started=datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"), log=[])
    log_.info("开始刷新（触发：%s）", trigger)
    log = Log()
    try:
        ds = deepstate.load_history(log)
        h = core.merge_history(ds, log) if ds else STATE["history"]
        if h:
            _set_history(h)
            D = today()
            days = [HISTORY_START + timedelta(days=i) for i in range((D - HISTORY_START).days + 1)]

            def prog(i, n, d):
                r.update(step=f"计算每日指标 {d}", done=i, total=n)
            core.update_metrics(h, days, log, progress=prog)
            r.update(step="预取近期快照")
            ids = {s["id"] for i in range(PREFETCH_DAYS + 1)
                   if (s := deepstate.snapshot_for_day(h, D - timedelta(days=i)))}
            for sid in sorted(ids):
                try:
                    core.layers_for(sid)
                except Exception as e:  # noqa: BLE001
                    log.warn("DeepState 快照", f"{sid}: {e}")
            log.ok("每日指标", f"覆盖 {HISTORY_START} 至 {D}")
        r.update(step="检查 ISW 与新闻源")
        try:
            res = isw.for_date(today(), today(), force=True)
            (log.ok if res["status"] == "ok" else log.warn)("ISW（criticalthreats.org）",
                                                            f"{res['source']} {res.get('note', '')}")
        except Exception as e:  # noqa: BLE001
            log.warn("ISW", str(e))
        try:
            res = news.for_date(today(), today(), force=True)
            for s in res["sources"]:
                log.items.append(s)
        except Exception as e:  # noqa: BLE001
            log.warn("新闻 RSS", str(e))
        r.update(step="检查 Kiel 援助数据与援助/政治新闻")
        try:
            aid.check_kiel(log, min_hours=float(os.environ.get("UF_KIEL_CHECK_HOURS", "24")))
        except Exception as e:  # noqa: BLE001
            log.warn("Kiel 援助数据", f"检查失败（保留已有数据）：{e}")
        try:
            aid.refresh_recent_news(log)
        except Exception as e:  # noqa: BLE001
            log.warn("援助/政治新闻", str(e))
        _cache_clear()
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        log.fail("刷新", str(e))
    finally:
        now = datetime.now(timezone.utc)
        try:
            v = _data_version()
        except Exception:  # noqa: BLE001
            v = SCHED["data_version"]
        changed = v != SCHED["data_version"]
        SCHED["data_version"] = v
        if changed:
            SCHED["last_new_data"] = now
        SCHED["last_run_end"] = now
        SCHED["runs"] += 1
        iv = SCHED["interval_min"]
        SCHED["next_run"] = now + timedelta(minutes=iv) if iv > 0 else None
        _save_sched_state()
        r.update(running=False, step="完成", log=log.items,
                 finished=datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"))
        levels = [x["level"] for x in log.items]
        log_.info("刷新完成（%s，用时 %.0f 秒）：%s；数据%s；下次 %s", trigger, (now - t0).total_seconds(),
                  ", ".join(f"{x['level']}:{x['source']}" for x in log.items if x["level"] != "OK") or "全部来源正常",
                  "有更新" if changed else "无变化",
                  _paris_hm(SCHED["next_run"]) if SCHED["next_run"] else "（自动更新已关闭）")
        _run_lock.release()
    return True


def _paris_hm(ts, with_date=False):
    if ts is None:
        return None
    return ts.astimezone(TZ).strftime("%m-%d %H:%M" if with_date else "%H:%M")


def start_refresh(trigger="manual"):
    if not _run_lock.locked():
        threading.Thread(target=refresh_job, args=(trigger,), daemon=True).start()


def _scheduler_loop():
    while True:
        _sched_wake.wait(timeout=15)
        _sched_wake.clear()
        nr = SCHED["next_run"]
        if nr is not None and datetime.now(timezone.utc) >= nr and not _run_lock.locked():
            refresh_job("startup" if SCHED["runs"] == 0 else "schedule")


def sched_status():
    def iso(x):
        return x.isoformat() if x else None
    return {"interval_min": SCHED["interval_min"], "interval_source": SCHED["interval_source"],
            "next_run": iso(SCHED["next_run"]), "next_run_paris": _paris_hm(SCHED["next_run"], True),
            "last_checked": iso(SCHED["last_run_end"]), "last_checked_paris": _paris_hm(SCHED["last_run_end"], True),
            "last_new_data": iso(SCHED["last_new_data"]),
            "last_new_data_paris": _paris_hm(SCHED["last_new_data"], True),
            "data_version": SCHED["data_version"], "runs": SCHED["runs"]}


def _cache_clear():
    _changes_cached.cache_clear()


# ------------------------------------------------------------------ helpers
def _snap(day):
    s = deepstate.snapshot_for_day(STATE["history"], day)
    if s is None:
        raise HTTPException(404, f"{day} 之前没有 DeepState 快照")
    return s


def _gj(g, tol=0.0003):
    if g is None or g.is_empty:
        return None
    g2 = shapely.set_precision(g.simplify(tol, preserve_topology=True), 1e-5)
    return mapping(g2)


@lru_cache(maxsize=64)
def _layers_payload(sid):
    lay = core.layers_for(sid)
    return {"occupied": _gj(lay["occupied"]), "grey": _gj(lay["grey"]),
            "occupied_km2": round(geo.area_km2(lay["occupied"]), 2),
            "grey_km2": round(geo.area_km2(lay["grey"]), 2)}


def _note(entry):
    text, places = deepstate.describe(entry)
    return {"id": entry["id"], "time": core.paris(entry["_ts"]), "text": text,
            "places": [{"name": n, "url": u} for n, u in places]}


@lru_cache(maxsize=1)
def _name_index():
    df = geo.places()
    idx = {}
    for r in df.itertuples():
        idx.setdefault(str(r.ascii).lower(), []).append((r.lon, r.lat))
    return idx


def _note_points(entry):
    """Coordinates referred to by a DeepState changelog entry."""
    _, places = deepstate.describe(entry)
    coords, names = [], []
    for name, url in places:
        m = deepstate._XYZ.match(url.split("#", 1)[1]) if "#" in url else None
        if m:
            coords.append((float(m.group(3)), float(m.group(2))))
        else:
            names.append(name)
    pts = list(coords)
    for name in names:
        cands = _name_index().get(name.lower(), [])
        if coords:  # disambiguate homonyms: keep candidates near the note's explicit coordinates
            cands = [c for c in cands if min(abs(c[0] - x) + abs(c[1] - y) for x, y in coords) < 0.6]
        elif len(cands) > 3:  # too ambiguous (common village names)
            cands = []
        pts += cands
    return pts


def _local(g):
    return transform(_to_local, g)


@lru_cache(maxsize=48)
def _changes_cached(cur_id, from_id, from_day, cur_day):
    hist = STATE["history"]
    cur, frm = STATE["by_id"][cur_id], STATE["by_id"][from_id]
    lc, lf = core.layers_for(cur_id), core.layers_for(from_id)
    d = core.pair_diff(from_id, cur_id)
    rows = geo.change_table(d["gained"], d["lost"], lf["grey"], lc["grey"])
    # DeepState notes in the window (by publication time)
    notes = [h for h in hist if frm["_ts"] < h["_ts"] <= cur["_ts"]]
    note_pts = [(h, [_local(Point(x, y)) for x, y in _note_points(h)]) for h in notes[-120:]]
    # daily pair diffs to date each change ("first seen")
    metrics = core.read_metrics()
    daily = []
    span = (date.fromisoformat(cur_day) - date.fromisoformat(from_day)).days
    for k in (sorted(metrics) if span <= 45 else []):   # per-day dating only for short windows
        if from_day < k <= cur_day:
            m = metrics[k]
            if m["prev_snapshot_id"] and m["prev_snapshot_id"] != m["snapshot_id"]:
                daily.append((k, m["snapshot_time_paris"], m["prev_snapshot_id"], m["snapshot_id"]))
    feats = []
    for i, r in enumerate(rows):
        g = r["geometry"]
        gl = _local(g)
        seen = []
        for k, t, a, b in daily:
            pdf = core.pair_diff(int(a), int(b), download=False)
            if pdf is None:
                continue
            part = pdf["gained" if r["kind"] == "gain" else "lost"]
            if not part.is_empty and g.intersects(part):
                ar = geo.area_km2(g.intersection(part))
                if ar >= 0.01:
                    seen.append({"date": k, "snapshot_time": t, "km2": round(ar, 2)})
        matched = []
        for h, pts in note_pts:
            if pts and min(gl.distance(p) for p in pts) <= 6000:
                matched.append(_note(h))
        feats.append({"type": "Feature", "geometry": mapping(shapely.set_precision(g, 1e-6)),
                      "properties": {"id": i, "kind": r["kind"], "area_km2": round(r["area_km2"], 3),
                                     "place": r["place"], "note": r["note"], "lat": r["lat"], "lon": r["lon"],
                                     "first_seen": seen, "deepstate_notes": matched[-3:],
                                     "deepstate_url": f"https://deepstatemap.live/en#14/{r['lat']:.5f}/{r['lon']:.5f}"}})
    secs = geo.sectors(rows)
    return {
        "from": {"id": from_id, "time": core.paris(frm["_ts"]), "day": from_day},
        "to": {"id": cur_id, "time": core.paris(cur["_ts"]), "day": cur_day},
        "stats": {"gained_km2": round(d["gained_km2"], 2), "lost_km2": round(d["lost_km2"], 2),
                  "net_km2": round(d["gained_km2"] - d["lost_km2"], 2),
                  "grey_change_km2": round(geo.area_km2(lc["grey"]) - geo.area_km2(lf["grey"]), 2),
                  "n_changes": len(rows)},
        "changes": {"type": "FeatureCollection", "features": feats},
        "sectors": [{k: v for k, v in s.items() if k != "town"} | {"town": s["town"]["name"]} for s in secs[:10]],
        "notes": [_note(h) for h in reversed(notes)][:40],
        "long_window": span > 45,
        "source_switch": core.source_of(from_id) != core.source_of(cur_id),
    }


# ------------------------------------------------------------------ API
@app.get("/api/meta")
def meta():
    m = core.read_metrics()
    h = STATE["history"]
    days = sorted(m)
    return {"days": [{"date": k, "snapshot_id": int(m[k]["snapshot_id"]), "time": m[k]["snapshot_time_paris"],
                      "updated": m[k]["updated_that_day"] == "True", "source": m[k].get("source") or "deepstate"}
                     for k in days],
            "schedule": sched_status(),
            "today": today().isoformat(), "history_count": len(h),
            "history_first": core.paris(h[0]["_ts"]) if h else None,
            "latest": _note(h[-1]) if h else None, "history_start": HISTORY_START.isoformat(),
            "deepstate_first": next((core.paris(x["_ts"]) for x in h if x["id"] > 0), None),
            "refresh": STATE["refresh"]}


@lru_cache(maxsize=1)
def _borders():
    c = geo.countries()
    c = c.cx[15:50, 40:58]
    feats = [{"type": "Feature", "properties": {"a3": r.ADM0_A3, "name": r.NAME},
              "geometry": mapping(shapely.set_precision(r.geometry.simplify(0.01).boundary, 1e-4))}
             for r in c.itertuples()]
    return {"type": "FeatureCollection", "features": feats}


@app.get("/api/borders")
def borders():
    return _borders()


@app.get("/api/status")
def status():
    return {**STATE["refresh"], "schedule": sched_status()}


@app.post("/api/refresh")
def refresh():
    start_refresh("manual")
    return {**STATE["refresh"], "running": True, "schedule": sched_status()}


class Settings(BaseModel):
    refresh_minutes: int


@app.post("/api/settings")
def settings(s: Settings):
    m = s.refresh_minutes
    if m != 0 and not 1 <= m <= 1440:
        raise HTTPException(400, "间隔须为 0（关闭）或 1–1440 分钟")
    SCHED["interval_min"], SCHED["interval_source"] = m, "ui"
    SETTINGS_FILE.write_text(json.dumps({"refresh_minutes": m}), encoding="utf-8")
    base = SCHED["last_run_end"] or datetime.now(timezone.utc)
    SCHED["next_run"] = max(base + timedelta(minutes=m), datetime.now(timezone.utc)) if m > 0 else None
    log_.info("自动更新间隔改为 %s 分钟（界面设置）", m)
    _sched_wake.set()
    return sched_status()


@app.get("/api/layers")
def layers(day: date = Query(..., alias="date")):
    s = _snap(day)
    try:
        p = _layers_payload(s["id"])
    except Exception as e:  # noqa: BLE001
        raise HTTPException(503, f"无法获取快照 {s['id']}：{e}")
    return {"snapshot": _note(s) | {"updated_that_day": s["_ts"].astimezone(TZ).date() == day,
                                    "source": core.source_of(s["id"])}, **p}


@app.get("/api/changes")
def changes(day: date = Query(..., alias="date"), compare: str = "1", frm: date | None = Query(None, alias="from")):
    cur = _snap(day)
    if compare == "custom":
        if frm is None:
            raise HTTPException(400, "自定义对比需要 from 日期")
        fday = frm
    elif compare == "prev":  # previous DeepState update
        idx = next(i for i, h in enumerate(STATE["history"]) if h["id"] == cur["id"])
        if idx == 0:
            raise HTTPException(404, "没有更早的快照")
        prev = STATE["history"][idx - 1]
        fday = prev["_ts"].astimezone(TZ).date()
    else:
        fday = day - timedelta(days=int(compare))
    if compare == "prev":
        frm_s = prev
    else:
        frm_s = _snap(fday)
    if frm_s["id"] == cur["id"]:
        return {"from": {"id": frm_s["id"], "time": core.paris(frm_s["_ts"]), "day": fday.isoformat()},
                "to": {"id": cur["id"], "time": core.paris(cur["_ts"]), "day": day.isoformat()},
                "stats": {"gained_km2": 0, "lost_km2": 0, "net_km2": 0, "grey_change_km2": 0, "n_changes": 0},
                "changes": {"type": "FeatureCollection", "features": []}, "sectors": [], "notes": [],
                "message": "两个日期对应同一个 DeepState 快照（期间未更新），无变化"}
    try:
        return _changes_cached(cur["id"], frm_s["id"], fday.isoformat(), day.isoformat())
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        raise HTTPException(503, f"计算变化失败：{e}")


@app.get("/api/timeline")
def timeline():
    m = core.read_metrics()
    out, cum = [], 0.0

    def f(x):
        return float(x) if x not in (None, "") else None
    for k in sorted(m):
        r = m[k]
        net = f(r["net_km2"])
        cum += net or 0.0
        out.append({"date": k, "source": r.get("source") or "deepstate",
                    "occupied_km2": f(r["occupied_km2"]), "grey_km2": f(r["grey_km2"]),
                    "gained_km2": f(r["gained_km2"]) or 0.0, "lost_km2": f(r["lost_km2"]) or 0.0,
                    "net_km2": net, "cum_net_km2": round(cum, 3), "note": r.get("note", ""),
                    "updated": r["updated_that_day"] == "True"})
    return out


@app.get("/api/isw")
def isw_api(day: date = Query(..., alias="date")):
    try:
        return isw.for_date(day, today())
    except Exception as e:  # noqa: BLE001
        return {"status": "fail", "items": [], "note": str(e)}


@app.get("/api/news")
def news_api(day: date = Query(..., alias="date")):
    try:
        return news.for_date(day, today())
    except Exception as e:  # noqa: BLE001
        return {"items": [], "sources": [{"level": "FAIL", "source": "新闻", "msg": str(e)}]}


AID_CATS = {"military": "军事", "financial": "经济/金融", "humanitarian": "人道", "political": "政治",
            "other": "其他"}


def _aid_meta():
    m = aid.read_meta()
    if m.get("coverage_end"):
        m["lag_days"] = (today() - date.fromisoformat(m["coverage_end"])).days
    for k in ("downloaded_utc", "checked_utc"):
        if m.get(k):
            m[k + "_paris"] = datetime.fromisoformat(m[k]).astimezone(TZ).strftime("%Y-%m-%d %H:%M")
    return m


@app.get("/api/aid")
def aid_api(day: date = Query(..., alias="date"), window: int = Query(3, ge=0, le=15),
            fetch: bool = True):
    d0, d1 = max(aid.WAR_START, day - timedelta(days=window)), min(today(), day + timedelta(days=window))
    meta = _aid_meta()
    ev = aid.events_between(d0, d1)
    try:
        nw, nst = aid.news_for_range(d0, d1, fetch_missing=fetch)
    except Exception as e:  # noqa: BLE001
        nw, nst = [], {"errors": [str(e)], "fetched_windows": 0}
    cov = meta.get("coverage_end")
    note = ""
    if cov and d1.isoformat() > cov:
        note = (f"Kiel 数据目前只覆盖到 {cov}（{meta.get('release', '')}），"
                f"之后的援助尚未录入；请参考下方新闻。")
    return {"from": d0.isoformat(), "to": d1.isoformat(), "aid": ev, "news": nw, "news_status": nst,
            "summary": aid.summary_until(day), "meta": meta, "note": note, "categories": AID_CATS}


@app.get("/api/aid/timeline")
def aid_timeline():
    return {"series": aid.cumulative_series(), "meta": _aid_meta()}


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


app.mount("/static", StaticFiles(directory=WEB), name="static")




def main():
    for st in (sys.stdout, sys.stderr):
        try:
            st.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=int(os.environ.get("UF_PORT", "8765")))
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    url = f"http://{'localhost' if a.host in ('127.0.0.1', '0.0.0.0') else a.host}:{a.port}/"
    if not a.no_browser:
        threading.Timer(2.0, lambda: webbrowser.open(url)).start()
    print(f"俄乌前线 OSINT 追踪器 运行于 {url}  （Ctrl+C 退出）")
    import uvicorn
    uvicorn.run(app, host=a.host, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
