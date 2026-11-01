"""Shared stateful helpers: snapshot layers, pairwise diffs, metrics history."""
import csv
import pickle
import threading
from datetime import timedelta

from . import deepstate, geo, isw_cot
from .config import DATA, METRICS_CSV, TZ

FIELDS = ["date", "source", "snapshot_id", "snapshot_time_paris", "updated_that_day", "occupied_km2",
          "grey_km2", "gained_km2", "lost_km2", "net_km2", "prev_snapshot_id", "note"]

_lock = threading.RLock()
_layer_mem = {}


def paris(ts):
    return ts.astimezone(TZ).strftime("%Y-%m-%d %H:%M")


def load_full_history(log=None):
    """DeepState public history (from 2022-04-03) preceded by ISW/CTP daily control-of-terrain
    pseudo-snapshots for 2022-02-24..2022-04-02. Sorted by time."""
    ds = deepstate.load_history(log) if log is not None else []
    return merge_history(ds, log)


def merge_history(ds, log=None):
    first = ds[0]["_ts"] if ds else None
    early = [e for e in isw_cot.history_entries(log) if first is None or e["_ts"] < first]
    for e in ds:
        e.setdefault("source", "deepstate")
    return early + ds


def source_of(sid):
    return "isw" if int(sid) < 0 else "deepstate"


def layers_cached(sid):
    if int(sid) < 0:
        return True
    return sid in _layer_mem or (DATA / f"layers_v{deepstate.LAYER_VERSION}" / f"{sid}.pkl").exists() or \
        (DATA / "snapshots" / f"{sid}.geojson.gz").exists()


def layers_for(sid, log=None):
    sid = int(sid)
    if sid in _layer_mem:
        return _layer_mem[sid]
    p = DATA / f"layers_v{deepstate.LAYER_VERSION}" / f"{sid}.pkl"
    lay = None
    if sid < 0:  # ISW 2022 pseudo-snapshot
        lay = isw_cot.layers(sid, geo.ukraine_clip())
        with _lock:
            _layer_mem[sid] = lay
        return lay
    if p.exists():
        try:
            lay = pickle.loads(p.read_bytes())
        except Exception:  # noqa: BLE001  (e.g. incompatible pickle) -> rebuild
            lay = None
    if lay is None:
        fc = deepstate.fetch_snapshot(sid, log)
        lay = deepstate.extract_layers(fc, geo.ukraine_clip())
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(pickle.dumps(lay))
    with _lock:
        if len(_layer_mem) > 60:
            _layer_mem.pop(next(iter(_layer_mem)))
        _layer_mem[sid] = lay
    return lay


def pair_diff(prev_id, cur_id, download=True):
    """Gained/lost geometries between two snapshots; cached on disk."""
    p = DATA / f"diffs_v{deepstate.LAYER_VERSION}" / f"{prev_id}_{cur_id}.pkl"
    if p.exists():
        try:
            return pickle.loads(p.read_bytes())
        except Exception:  # noqa: BLE001
            pass
    if not download and not (layers_cached(int(prev_id)) and layers_cached(int(cur_id))):
        return None
    a, b = layers_for(prev_id), layers_for(cur_id)
    g, l_ = geo.diff(a["occupied"], b["occupied"])
    d = {"gained": g, "lost": l_, "gained_km2": geo.area_km2(g), "lost_km2": geo.area_km2(l_)}
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(pickle.dumps(d))
    return d


def read_metrics():
    if not METRICS_CSV.exists():
        return {}
    with METRICS_CSV.open(encoding="utf-8") as f:
        return {r["date"]: r for r in csv.DictReader(f)}


def write_metrics(rows):
    METRICS_CSV.parent.mkdir(parents=True, exist_ok=True)
    tmp = METRICS_CSV.with_suffix(".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for d in sorted(rows):
            w.writerow({k: rows[d].get(k, "") for k in FIELDS})
    tmp.replace(METRICS_CSV)


def update_metrics(history, days, log, progress=None):
    with _lock:
        rows = read_metrics()
    failed = []
    for i, d in enumerate(days):
        if progress:
            progress(i, len(days), d)
        snap = deepstate.snapshot_for_day(history, d)
        prev = deepstate.snapshot_for_day(history, d - timedelta(days=1))
        if snap is None:
            continue
        key = d.isoformat()
        sid, pid = str(snap["id"]), str(prev["id"]) if prev else ""
        old = rows.get(key)
        if old and old.get("source") and old["snapshot_id"] == sid and old["prev_snapshot_id"] == pid:
            continue  # idempotent: already computed with the same inputs
        try:
            cur = layers_for(snap["id"], log)
            row = {"date": key, "snapshot_id": sid, "snapshot_time_paris": paris(snap["_ts"]),
                   "updated_that_day": snap["_ts"].astimezone(TZ).date() == d,
                   "occupied_km2": round(geo.area_km2(cur["occupied"]), 3),
                   "grey_km2": round(geo.area_km2(cur["grey"]), 3), "prev_snapshot_id": pid}
            row["source"] = source_of(snap["id"])
            bf = cur.get("baseline_filled") or []
            row["note"] = ("DeepState 该快照未绘制" + "/".join({"crimea": "克里米亚", "ordlo": "2014年后占领区"}[k] for k in bf)
                           + "，已用基线补齐") if bf else ""
            if prev and pid != sid and source_of(prev["id"]) != source_of(snap["id"]):
                # methodology break (ISW -> DeepState): no meaningful day-over-day diff
                row.update(gained_km2="", lost_km2="", net_km2="",
                           note=("数据源切换：ISW→DeepState，口径不同，不计算日变化；" + row["note"]).rstrip("；"))
                rows[key] = row
                continue
            if prev and pid != sid:
                dd = pair_diff(prev["id"], snap["id"])
                row.update(gained_km2=round(dd["gained_km2"], 3), lost_km2=round(dd["lost_km2"], 3))
            else:
                row.update(gained_km2=0.0, lost_km2=0.0)
            row["net_km2"] = round(float(row["gained_km2"]) - float(row["lost_km2"]), 3)
            rows[key] = row
            if i % 10 == 0:
                with _lock:
                    write_metrics(rows)
        except Exception as e:  # noqa: BLE001
            failed.append(f"{key}: {e}")
    with _lock:
        write_metrics(rows)
    if failed and log:
        log.warn("DeepState 快照下载/计算", f"{len(failed)} 天失败，例如 " + "; ".join(failed[:3]))
    return rows
