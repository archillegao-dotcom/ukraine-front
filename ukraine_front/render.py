"""Map and timeline rendering."""
import math

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.patches import Patch, Rectangle  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from shapely.geometry import box  # noqa: E402

from . import geo  # noqa: E402

plt.rcParams["font.family"] = ["Noto Sans CJK SC", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

C_OCC, C_GREY, C_GAIN, C_LOSS = "#f2b8b5", "#c9c3bd", "#c0000b", "#1565c0"
C_GAIN7, C_LOSS7 = "#f08080", "#90caf9"
MAIN_EXTENT = (22.0, 40.4, 44.2, 52.5)


def _plot_geom(ax, g, **kw):
    if g is None or g.is_empty:
        return
    gs = geo.gpd.GeoSeries([g], crs="EPSG:4326")
    gs.plot(ax=ax, **kw)


def _cities(min_pop=250000):
    df = geo.places()
    return df[(df["pop"] >= min_pop) | (df.fcode == "PPLC")]


def _lonlat_box(s, min_km=22.0, pad=0.25):
    minx, miny, maxx, maxy = s["bounds"]
    cx, cy = (minx + maxx) / 2, (miny + maxy) / 2
    kx = 111.32 * math.cos(math.radians(cy))
    w = max((maxx - minx) * kx * (1 + pad), min_km) / kx
    h = max((maxy - miny) * 111.32 * (1 + pad), min_km) / 111.32
    side_w, side_h = max(w, h * 111.32 / kx), max(h, w * kx / 111.32)
    return cx - side_w / 2, cx + side_w / 2, cy - side_h / 2, cy + side_h / 2


def front_map(path, *, title, subtitle, layers, layers_prev7, chg, chg7, secs, attribution):
    occ, grey = layers["occupied"], layers["grey"]
    fig = plt.figure(figsize=(18, 11.5), dpi=110)
    ax = fig.add_axes([0.01, 0.07, 0.64, 0.83])
    c = geo.countries()
    c.plot(ax=ax, color="#eeeeee", edgecolor="#9e9e9e", linewidth=0.5)
    c[c.ADM0_A3 == "UKR"].plot(ax=ax, color="#fbfaf5", edgecolor="#424242", linewidth=1.0)
    _plot_geom(ax, occ, color=C_OCC, edgecolor="#b71c1c", linewidth=0.4)
    _plot_geom(ax, grey, color=C_GREY, edgecolor="none")
    _plot_geom(ax, chg7["gained"], color=C_GAIN7, edgecolor="none")
    _plot_geom(ax, chg7["lost"], color=C_LOSS7, edgecolor="none")
    _plot_geom(ax, chg["gained"], color=C_GAIN, edgecolor=C_GAIN, linewidth=0.6)
    _plot_geom(ax, chg["lost"], color=C_LOSS, edgecolor=C_LOSS, linewidth=0.6)
    # change markers so small daily changes stay visible at country scale
    for r in chg["rows"]:
        col = C_GAIN if r["kind"] == "gain" else C_LOSS
        ax.scatter(r["lon"], r["lat"], s=40 + 160 * math.sqrt(r["area_km2"]),
                   facecolors="none", edgecolors=col, linewidths=1.6, zorder=6)
    for _, p in _cities().iterrows():
        ax.plot(p.lon, p.lat, "o", ms=3, color="#212121", zorder=7)
        ax.text(p.lon + 0.07, p.lat + 0.05, p.ascii, fontsize=7.5, color="#212121", zorder=7)
    ax.set_xlim(*MAIN_EXTENT[:2]); ax.set_ylim(*MAIN_EXTENT[2:])
    ax.set_aspect(1 / math.cos(math.radians(48.5)))
    ax.set_xticks([]); ax.set_yticks([]); ax.set_xlabel(""); ax.set_ylabel("")
    ax.set_facecolor("#dbe9f4")

    # insets
    n = min(3, len(secs))
    for i, s in enumerate(secs[:n]):
        x0, x1, y0, y1 = _lonlat_box(s)
        ax.add_patch(Rectangle((x0, y0), x1 - x0, y1 - y0, fill=False, ec="black", lw=1.3, zorder=8))
        ax.text(x1, y1, f" {i + 1}", fontsize=11, weight="bold", zorder=8, va="bottom")
        h = 0.83 / 3
        iax = fig.add_axes([0.665, 0.07 + (2 - i) * h + 0.01, 0.32, h - 0.03])
        bb = box(x0, y0, x1, y1)
        c.plot(ax=iax, color="#fbfaf5", edgecolor="#616161", linewidth=0.6)
        _plot_geom(iax, layers_prev7["occupied"].intersection(bb), color=C_OCC, edgecolor="none")
        _plot_geom(iax, occ.intersection(bb), color=C_OCC, edgecolor="#b71c1c", linewidth=0.5)
        _plot_geom(iax, grey.intersection(bb), color=C_GREY, edgecolor="none")
        _plot_geom(iax, chg7["gained"].intersection(bb), color=C_GAIN7, edgecolor="none")
        _plot_geom(iax, chg7["lost"].intersection(bb), color=C_LOSS7, edgecolor="none")
        _plot_geom(iax, chg["gained"].intersection(bb), color=C_GAIN, edgecolor=C_GAIN, linewidth=0.5)
        _plot_geom(iax, chg["lost"].intersection(bb), color=C_LOSS, edgecolor=C_LOSS, linewidth=0.5)
        pl = geo.places()
        sub = pl[(pl.lon.between(x0, x1)) & (pl.lat.between(y0, y1))].nlargest(10, "pop")
        for _, p in sub.iterrows():
            iax.plot(p.lon, p.lat, "o", ms=2.5, color="#212121")
            iax.text(p.lon + 0.005, p.lat + 0.004, p.ascii, fontsize=7, color="#212121")
        iax.set_xlim(x0, x1); iax.set_ylim(y0, y1)
        iax.set_aspect(1 / math.cos(math.radians((y0 + y1) / 2)))
        iax.set_xticks([]); iax.set_yticks([]); iax.set_xlabel(""); iax.set_ylabel("")
        iax.set_facecolor("#fbfaf5")
        iax.set_title(f"{i + 1}. {s['name']}（{s['oblast']}）  俄+{s['gain']:.2f} / 乌+{s['loss']:.2f} km²",
                      fontsize=10, loc="left")
    if n == 0:
        fig.text(0.68, 0.5, "所选时段内无可识别的前线变化", fontsize=13)

    handles = [Patch(color=C_OCC, label="俄控区（DeepState：占领 + 克里米亚/2014年后占领区）"),
               Patch(color=C_GREY, label="灰色区 / 状态不明"),
               Patch(color=C_GAIN, label=f"俄方新增（{chg['label']}）"),
               Patch(color=C_LOSS, label=f"乌方收复/俄控减少（{chg['label']}）"),
               Patch(color=C_GAIN7, label="俄方新增（近7天）"),
               Patch(color=C_LOSS7, label="俄控减少（近7天）"),
               Line2D([], [], marker="o", ls="", mfc="none", mec=C_GAIN, label="变化位置标记（大小∝面积）")]
    ax.legend(handles=handles, loc="lower left", fontsize=8.5, framealpha=0.92)
    fig.text(0.01, 0.965, title, fontsize=19, weight="bold")
    fig.text(0.01, 0.935, subtitle, fontsize=11, color="#424242")
    fig.text(0.01, 0.02, attribution, fontsize=8, color="#616161", wrap=True)
    fig.savefig(path, dpi=110)
    plt.close(fig)


def timeline(path, df, title):
    df = df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date")
    df["cum_net"] = df["net_km2"].fillna(0).cumsum()
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(13, 8), sharex=True, dpi=110,
                                 gridspec_kw={"height_ratios": [1, 1.2]})
    a1.plot(df.date, df.occupied_km2, color="#b71c1c", lw=2, marker="o", ms=3)
    a1.set_ylabel("俄控面积 km²")
    a1.ticklabel_format(axis="y", useOffset=False, style="plain")
    a1.grid(alpha=0.3)
    a1.set_title(title, fontsize=14, loc="left")
    a2.bar(df.date, df.gained_km2, color="#c0000b", label="俄方新增 km²")
    a2.bar(df.date, -df.lost_km2, color="#1565c0", label="俄控减少 km²（负值）")
    a2b = a2.twinx()
    a2b.plot(df.date, df.cum_net, color="black", lw=2, label="累计净变化 km²")
    a2b.set_ylabel("累计净变化 km²")
    a2.set_ylabel("每日变化 km²")
    a2.axhline(0, color="#757575", lw=0.6)
    a2.grid(alpha=0.3)
    h1, l1 = a2.get_legend_handles_labels(); h2, l2 = a2b.get_legend_handles_labels()
    a2.legend(h1 + h2, l1 + l2, loc="upper left", fontsize=9)
    a2.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d"))
    fig.text(0.01, 0.005, "数据：DeepStateMap 公开历史快照（按欧洲/巴黎时区日终最新快照）；面积用 EPSG:6933 等积投影计算。"
             "未更新的日子变化记为 0；地图可能滞后或回溯修订。", fontsize=8, color="#616161")
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(path)
    plt.close(fig)
