"""Static reference layers, projections and change analysis."""
import math
import zipfile
from functools import lru_cache

import geopandas as gpd
import numpy as np
import pandas as pd
from pyproj import Transformer
from shapely.geometry import MultiPolygon, Point
from shapely.ops import transform, unary_union
from shapely.strtree import STRtree

from . import net
from .config import (EQUAL_AREA_CRS, GEONAMES_ADMIN1, GEONAMES_UA, LOCAL_CRS, MIN_CHANGE_KM2,
                     NE_COUNTRIES, SECTOR_CLUSTER_KM, STATIC)

_to_ea = Transformer.from_crs("EPSG:4326", EQUAL_AREA_CRS, always_xy=True).transform
_to_local = Transformer.from_crs("EPSG:4326", LOCAL_CRS, always_xy=True).transform
_from_local = Transformer.from_crs(LOCAL_CRS, "EPSG:4326", always_xy=True).transform


def area_km2(g):
    if g is None or g.is_empty:
        return 0.0
    return transform(_to_ea, g).area / 1e6


def _download(url, dest):
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(net.get(url, timeout=120).content)
    return dest


@lru_cache
def countries():
    p = _download(NE_COUNTRIES, STATIC / "ne_10m_admin_0_countries_ukr.zip")
    return gpd.read_file(f"zip://{p}")[["ADM0_A3", "NAME", "geometry"]]


@lru_cache
def ukraine():
    c = countries()
    return unary_union(c[c.ADM0_A3 == "UKR"].geometry.values)


@lru_cache
def ukraine_clip():
    """Ukraine (Natural Earth, Ukrainian point of view incl. Crimea) + 1 km tolerance."""
    u = transform(_to_local, ukraine()).buffer(1000)
    return transform(_from_local, u)


@lru_cache
def places():
    z = _download(GEONAMES_UA, STATIC / "UA.zip")
    adm = _download(GEONAMES_ADMIN1, STATIC / "admin1CodesASCII.txt")
    with zipfile.ZipFile(z) as zf, zf.open("UA.txt") as f:
        df = pd.read_csv(f, sep="\t", header=None, usecols=[1, 2, 4, 5, 6, 7, 10, 14],
                         names=["name", "ascii", "lat", "lon", "fclass", "fcode", "admin1", "pop"],
                         dtype={"admin1": str}, quoting=3, keep_default_na=False)
    df = df[(df.fclass == "P") & (~df.fcode.isin(["PPLH", "PPLQ", "PPLW", "PPLX"]))].copy()
    a1 = pd.read_csv(adm, sep="\t", header=None, names=["code", "n", "ascii", "gid"],
                     keep_default_na=False)
    a1 = dict(zip(a1.code, a1.ascii))
    df["oblast"] = [a1.get(f"UA.{c}", "") for c in df.admin1]
    df["pop"] = pd.to_numeric(df["pop"], errors="coerce").fillna(0).astype(int)
    xy = [transform(_to_local, Point(lo, la)) for lo, la in zip(df.lon, df.lat)]
    df["x"] = [p.x for p in xy]
    df["y"] = [p.y for p in xy]
    return df.reset_index(drop=True)


@lru_cache
def _tree(min_pop):
    df = places()
    sub = df[df["pop"] >= min_pop].reset_index(drop=True)
    pts = [Point(x, y) for x, y in zip(sub.x, sub.y)]
    return STRtree(pts), sub


def nearest_place(lon, lat, min_pop=0):
    tree, sub = _tree(min_pop)
    p = transform(_to_local, Point(lon, lat))
    i = int(tree.nearest(p))
    row = sub.iloc[i]
    dx, dy = p.x - row.x, p.y - row.y
    dist = math.hypot(dx, dy) / 1000
    bearing = (math.degrees(math.atan2(dx, dy)) + 360) % 360  # direction FROM place TO point
    dirs = ["北", "东北", "东", "东南", "南", "西南", "西", "西北"]
    return {"name": row.ascii or row["name"], "oblast": row.oblast, "pop": int(row["pop"]),
            "dist_km": dist, "dir_zh": dirs[int((bearing + 22.5) // 45) % 8]}


def place_label(np_):
    if np_["dist_km"] < 1.0:
        return f"{np_['name']}（{np_['oblast']}）附近"
    return f"{np_['name']} {np_['dir_zh']}约{np_['dist_km']:.1f} km（{np_['oblast']}）"


def diff(prev, cur):
    """Return (gained_by_russia, regained_by_ukraine) geometries, EPSG:4326."""
    empty = MultiPolygon()
    if prev is None or cur is None:
        return empty, empty
    return cur.difference(prev), prev.difference(cur)


def _parts(g):
    if g is None or g.is_empty:
        return []
    if g.geom_type == "Polygon":
        return [g]
    return [p for p in getattr(g, "geoms", []) if p.geom_type == "Polygon"]


def change_table(gained, lost, prev_grey=None, cur_grey=None):
    rows = []
    for kind, g in (("gain", gained), ("loss", lost)):
        for p in _parts(g):
            a = area_km2(p)
            if a < MIN_CHANGE_KM2:
                continue
            c = p.representative_point()
            npl = nearest_place(c.x, c.y)
            note = ""
            if kind == "gain" and prev_grey is not None and not prev_grey.is_empty:
                share = area_km2(p.intersection(prev_grey)) / a
                note = f"其中{share:.0%}此前为灰色区" if share > 0.05 else ""
            if kind == "loss" and cur_grey is not None and not cur_grey.is_empty:
                share = area_km2(p.intersection(cur_grey)) / a
                note = f"其中{share:.0%}转为灰色区" if share > 0.05 else ""
            rows.append({"kind": kind, "area_km2": a, "lon": c.x, "lat": c.y,
                         "place": place_label(npl), "oblast": npl["oblast"], "note": note,
                         "geometry": p})
    rows.sort(key=lambda r: -r["area_km2"])
    return rows


def sectors(rows):
    """Cluster change polygons within SECTOR_CLUSTER_KM into 'sectors'."""
    if not rows:
        return []
    half = SECTOR_CLUSTER_KM * 500
    buf = [transform(_to_local, r["geometry"]).buffer(half) for r in rows]
    blobs = _parts(unary_union(buf))
    out = []
    for b in blobs:
        members = [r for r, bb in zip(rows, buf) if bb.intersects(b)]
        gain = sum(r["area_km2"] for r in members if r["kind"] == "gain")
        loss = sum(r["area_km2"] for r in members if r["kind"] == "loss")
        w = np.array([r["area_km2"] for r in members])
        lon = float(np.average([r["lon"] for r in members], weights=w))
        lat = float(np.average([r["lat"] for r in members], weights=w))
        town = nearest_place(lon, lat, min_pop=3000)
        geoms = unary_union([r["geometry"] for r in members])
        out.append({"name": f"{town['name']} 方向", "oblast": town["oblast"],
                    "town": town, "gain": gain, "loss": loss, "net": gain - loss,
                    "activity": gain + loss, "n": len(members), "lon": lon, "lat": lat,
                    "bounds": geoms.bounds})
    out.sort(key=lambda s: -s["activity"])
    return out
