"""DeepStateMap public history + snapshot geometry handling."""
import json
import re
from datetime import datetime, time, timedelta, timezone
from html import unescape

from shapely.geometry import shape
from shapely.ops import unary_union
from shapely.validation import make_valid

from . import net
from .config import (CACHE, DEEPSTATE_GEOJSON, DEEPSTATE_HISTORY, SNAPSHOTS, TZ)

OCCUPIED_TAGS = ("geoJSON.status.occupied", "geoJSON.territories.crimea",
                 "geoJSON.territories.ordlo", "geoJSON.territories.tuzla")
GREY_TAGS = ("geoJSON.status.unknown",)
# DeepState's feature names changed over time (2022 snapshots have plain Ukrainian names
# without "geoJSON.status.*" tags), but its fill colours have been stable since April 2022:
OCCUPIED_FILLS = {"#a52714", "#880e4f"}   # occupied since 2022 / Crimea, ORDLO, Tuzla
OCCUPIED_BLACK = "#000000"                 # Apr-Jun 2022 ORDLO / Tuzla were drawn in black
GREY_FILLS = {"#bcaaa4", "#bdbdbd", "#757575"}  # unknown status / "під питанням" (contested)
# Everything else (green/blue "liberated", #ff5252 satirical areas outside Ukraine, etc.) is ignored.
# Feature names are partly corrupted in DeepState's archive (e.g. 'Окуп��вано'), so fills are primary.
LAYER_VERSION = 4


def parse_ts(s):
    return datetime.strptime(s.replace("Z", "+00:00"), "%Y-%m-%dT%H:%M:%S.%f%z") \
        if "." in s else datetime.fromisoformat(s.replace("Z", "+00:00"))


def load_history(log):
    """Return list of history entries sorted by createdAt. Falls back to cache."""
    cache = CACHE / "deepstate_history.json"
    try:
        data = net.get(DEEPSTATE_HISTORY).json()
        if not isinstance(data, list) or not data:
            raise RuntimeError("unexpected history payload")
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        log.ok("DeepState history API", f"{len(data)} snapshots listed")
    except Exception as e:  # noqa: BLE001
        if cache.exists():
            data = json.loads(cache.read_text(encoding="utf-8"))
            log.warn("DeepState history API", f"live fetch failed ({e}); using cached list "
                                                f"({len(data)} entries)")
        else:
            log.fail("DeepState history API", str(e))
            return []
    for d in data:
        d["_ts"] = parse_ts(d["createdAt"])
    return sorted(data, key=lambda d: d["_ts"])


def snapshot_for_day(history, day):
    """Latest snapshot created on or before the end of `day` (Europe/Paris)."""
    end = datetime.combine(day + timedelta(days=1), time(0), TZ).astimezone(timezone.utc)
    cands = [h for h in history if h["_ts"] < end]
    return cands[-1] if cands else None


def fetch_snapshot(sid, log=None):
    path = SNAPSHOTS / f"{sid}.geojson.gz"
    if path.exists():
        return net.read_json_gz(path)
    data = net.get(DEEPSTATE_GEOJSON.format(id=sid), timeout=90).json()
    if data.get("type") != "FeatureCollection":
        raise RuntimeError(f"snapshot {sid}: unexpected payload")
    net.write_json_gz(path, data)
    return data


def _poly_parts(geom):
    """Drop Z, keep polygonal parts only."""
    g = make_valid(shape(geom))
    if g.geom_type in ("Polygon", "MultiPolygon"):
        return [g]
    if hasattr(g, "geoms"):
        return [p for p in g.geoms if p.geom_type in ("Polygon", "MultiPolygon")]
    return []


def _baseline():
    """Crimea + pre-2022 occupied Donbas (ORDLO) reference polygons, taken from a recent
    DeepState snapshot (geoJSON.territories.crimea / .ordlo). Used only to fill snapshots in which
    DeepState did not draw these long-occupied areas (some 2022 snapshots)."""
    from .config import STATIC
    import json as _j
    p = STATIC / "deepstate_baseline_crimea_ordlo.geojson"
    if not p.exists():
        return None
    fc = _j.loads(p.read_text(encoding="utf-8"))
    return {f["properties"]["kind"]: make_valid(shape(f["geometry"])) for f in fc["features"]}


_BASE = None


def extract_layers(fc, ukraine_clip):
    """Return dict(occupied=geom, grey=geom, baseline_filled=[...]) in EPSG:4326, clipped to Ukraine."""
    global _BASE
    occ, grey = [], []
    for f in fc.get("features", []):
        g = f.get("geometry") or {}
        # 2022 snapshots often wrap polygons in GeometryCollections (KML MultiGeometry)
        subs = g.get("geometries", []) if g.get("type") == "GeometryCollection" else [g]
        subs = [x for x in subs if x and x.get("type") in ("Polygon", "MultiPolygon")]
        if not subs:
            continue
        props = f.get("properties") or {}
        name = props.get("name", "")
        fill = str(props.get("fill", "")).lower()
        for sub in subs:
            _classify(sub, name, fill, occ, grey)
    return _finish(occ, grey, ukraine_clip)


def _classify(g, name, fill, occ, grey):
        g2 = {"type": g["type"], "coordinates": _strip_z(g["coordinates"])}
        if any(t in name for t in OCCUPIED_TAGS) or fill in OCCUPIED_FILLS:
            occ += _poly_parts(g2)
        elif any(t in name for t in GREY_TAGS) or fill in GREY_FILLS:
            grey += _poly_parts(g2)
        elif fill == OCCUPIED_BLACK:
            (grey if "питанн" in name else occ).extend(_poly_parts(g2))


def _finish(occ, grey, ukraine_clip):
    global _BASE
    o = unary_union(occ).intersection(ukraine_clip) if occ else None
    filled = []
    if _BASE is None:
        _BASE = _baseline() or {}
    for kind, ref in _BASE.items():
        have = o.intersection(ref).area if o is not None and not o.is_empty else 0.0
        if have < 0.9 * ref.area:
            o = ref if o is None or o.is_empty else unary_union([o, ref])
            filled.append(kind)
    gr = unary_union(grey).intersection(ukraine_clip) if grey else None
    if o is not None and gr is not None:
        gr = gr.difference(o)
    return {"occupied": _polys_only(o), "grey": _polys_only(gr), "baseline_filled": filled}


def _polys_only(g):
    from shapely.geometry import MultiPolygon
    if g is None or g.is_empty:
        return MultiPolygon()
    if g.geom_type in ("Polygon", "MultiPolygon"):
        return g
    parts = [p for p in getattr(g, "geoms", []) if p.geom_type in ("Polygon", "MultiPolygon")]
    return unary_union(parts) if parts else MultiPolygon()


def _strip_z(c):
    if isinstance(c[0], (int, float)):
        return c[:2]
    return [_strip_z(x) for x in c]


_A = re.compile(r'<a href="(https://deepstatemap\.live/(?:en)?#([^"]*))"[^>]*>(.*?)</a>', re.S)
_XYZ = re.compile(r'^(\d+)/([\d.]+)/([\d.]+)$')


def describe(entry):
    """Plain-text DeepState changelog note (English if provided) + linked places [(name, url)]."""
    raw = entry.get("descriptionEn") or entry.get("description") or ""
    places = []
    for m in _A.finditer(raw):
        name = unescape(re.sub("<[^>]+>", "", m.group(3))).strip()
        xyz = _XYZ.match(m.group(2))
        url = (f"https://deepstatemap.live/en#{xyz.group(1)}/{xyz.group(2)}/{xyz.group(3)}"
               if xyz else m.group(1))
        places.append((name, url))
    text = unescape(re.sub(r"<[^>]+>", "", raw)).strip()
    text = re.sub(r"\s+", " ", text)
    return text, places
