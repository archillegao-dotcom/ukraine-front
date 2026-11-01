"""ISW / AEI-CTP 'Assessed Control of Terrain' for 2022-02-24 .. 2022-04-02.

DeepStateMap's public history starts on 2022-04-03, so the first weeks of the full-scale
invasion are taken from ISW's public ArcGIS FeatureServer layer
"Russian Assessed Control of Terrain February to November" (copyright: Institute for the
Study of War and AEI's Critical Threats Project). Each ISW date becomes a pseudo-snapshot
with a negative id (-YYYYMMDD) so the rest of the pipeline can treat it like a DeepState
snapshot. Only polygons are used; grey zone is not available in this layer.
"""
import gzip
import json
from datetime import date, datetime, timedelta, timezone

from shapely.geometry import MultiPolygon, shape
from shapely.ops import unary_union
from shapely.validation import make_valid

from . import net
from .config import STATIC

LAYER = ("https://services5.arcgis.com/SaBe5HMtmnbqSWlu/ArcGIS/rest/services/"
         "Russian_Assessed_Control_of_Terrain_February_to_November/FeatureServer/9")
ITEM_URL = "https://www.arcgis.com/home/item.html?id=963c5626435e49b98ae1db399859d04e"
START, END = date(2022, 2, 24), date(2022, 4, 2)   # DeepState public history begins 2022-04-03
CACHE_FILE = STATIC / "isw_cot_2022-02-24_2022-04-02.geojson.gz"
SOURCE_NAME = "ISW/CTP 评估控制区（ArcGIS）"


def _download():
    feats, off = [], 0
    where = (f"datetime >= TIMESTAMP '{START} 00:00:00' AND "
             f"datetime <= TIMESTAMP '{END} 00:00:00'")
    while True:
        r = net.get(LAYER + "/query", timeout=180, params={
            "where": where, "outFields": "datetime", "outSR": 4326, "f": "geojson",
            "geometryPrecision": 5, "maxAllowableOffset": 0.0003,
            "resultOffset": off, "resultRecordCount": 200}).json()
        fs = r.get("features", [])
        feats += fs
        off += len(fs)
        if not fs or (len(fs) < 200 and not r.get("properties", {}).get("exceededTransferLimit")):
            break
    if not feats:
        raise RuntimeError("ISW ArcGIS layer returned no features")
    # dissolve per day -> one compact feature per date (keeps the shipped file small)
    from shapely.geometry import mapping
    import shapely
    by = {}
    for ft in feats:
        if not ft.get("geometry") or ft["properties"].get("datetime") is None:
            continue
        d = datetime.fromtimestamp(ft["properties"]["datetime"] / 1000, timezone.utc).date()
        by.setdefault(d, []).append(make_valid(shape(ft["geometry"])))
    out = []
    for d in sorted(by):
        g = shapely.set_precision(unary_union(by[d]), 1e-5)
        out.append({"type": "Feature", "properties": {"date": d.isoformat()}, "geometry": mapping(g)})
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(CACHE_FILE, "wt", encoding="utf-8") as f:
        json.dump({"type": "FeatureCollection", "source": LAYER,
                   "note": "dissolved per day; geometry generalised to ~30 m", "features": out}, f)


_by_day = None


def _load():
    global _by_day
    if _by_day is not None:
        return _by_day
    if not CACHE_FILE.exists():
        _download()
    with gzip.open(CACHE_FILE, "rt", encoding="utf-8") as f:
        fc = json.load(f)
    by = {}
    for ft in fc["features"]:
        if not ft.get("geometry"):
            continue
        d = date.fromisoformat(ft["properties"]["date"])
        by.setdefault(d, []).append(make_valid(shape(ft["geometry"])))
    _by_day = by
    return by


def history_entries(log=None):
    """Pseudo history entries (same keys as DeepState history items)."""
    try:
        by = _load()
    except Exception as e:  # noqa: BLE001
        if log:
            log.warn("ISW 2022 年初控制区 (ArcGIS)", f"不可用：{e}；2022-02-24 至 04-02 将缺失")
        return []
    out = []
    for d in sorted(by):
        if START <= d <= END:
            ts = datetime(d.year, d.month, d.day, 20, 0, tzinfo=timezone.utc)
            out.append({"id": -int(d.strftime("%Y%m%d")), "_ts": ts, "createdAt": ts.isoformat(),
                        "source": "isw",
                        "description": f"ISW 评估的俄军控制区（{d}，ISW/CTP ArcGIS 图层）",
                        "descriptionEn": f"ISW assessed Russian control of terrain, {d} "
                                         f"(ISW/CTP ArcGIS layer; DeepState history starts 2022-04-03)"})
    if log and out:
        log.ok("ISW 2022 年初控制区 (ArcGIS)", f"{len(out)} 天（{out[0]['_ts'].date()} 至 {out[-1]['_ts'].date()}）")
    return out


def layers(sid, ukraine_clip):
    d = datetime.strptime(str(-int(sid)), "%Y%m%d").date()
    parts = _load().get(d)
    if not parts:
        raise RuntimeError(f"ISW 控制区数据中没有 {d}")
    occ = unary_union(parts).intersection(ukraine_clip)
    if occ.geom_type not in ("Polygon", "MultiPolygon"):
        occ = unary_union([g for g in getattr(occ, "geoms", []) if g.geom_type in ("Polygon", "MultiPolygon")])
    return {"occupied": occ, "grey": MultiPolygon()}
