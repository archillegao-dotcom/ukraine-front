"""Entry point: python -m ukraine_front.daily [--date YYYY-MM-DD] [--backfill 30]"""
import argparse
import json
import sys
import traceback
from datetime import date, datetime, timedelta, timezone

from shapely.geometry import MultiPolygon, mapping

from . import deepstate, geo, isw, news, render
from .config import OUTPUT, TZ



class Log:
    def __init__(self):
        self.items = []

    def _add(self, lvl, src, msg):
        self.items.append((lvl, src, msg))
        print(f"[{lvl}] {src}: {msg}", file=sys.stderr)

    def ok(self, s, m): self._add("OK", s, m)
    def warn(self, s, m): self._add("WARN", s, m)
    def fail(self, s, m): self._add("FAIL", s, m)


from .core import layers_for, read_metrics, update_metrics, write_metrics  # noqa: E402,F401
from .core import paris as _paris  # noqa: E402
from .core import load_full_history as core_load_full_history  # noqa: E402


def change_bundle(prev_l, cur_l, label):
    g, l_ = geo.diff(prev_l["occupied"], cur_l["occupied"])
    rows = geo.change_table(g, l_, prev_l["grey"], cur_l["grey"])
    return {"gained": g, "lost": l_, "gain_km2": geo.area_km2(g), "loss_km2": geo.area_km2(l_),
            "rows": rows, "label": label}


def fmt(x, sign=True):
    x = 0.0 if abs(x) < 0.005 else x
    return f"{x:+,.2f}" if sign else f"{x:,.2f}"


def main(argv=None):
    for st in (sys.stdout, sys.stderr):
        try:
            st.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", help="report date (Europe/Paris), default today")
    ap.add_argument("--backfill", type=int, default=30, help="days of metrics to (re)compute")
    ap.add_argument("--no-news", action="store_true")
    a = ap.parse_args(argv)
    now = datetime.now(timezone.utc)
    D = date.fromisoformat(a.date) if a.date else now.astimezone(TZ).date()
    out = OUTPUT / D.isoformat()
    out.mkdir(parents=True, exist_ok=True)
    log = Log()

    history = core_load_full_history(log)
    metrics, ctx = {}, {}
    if history:
        days = [D - timedelta(days=i) for i in range(max(a.backfill, 30), -1, -1)]
        metrics = update_metrics(history, days, log)
        ctx = analyse(history, D, log)
    isw_items = isw.latest(D, log)
    heads = [] if a.no_news else news.headlines(now, log)

    files = {}
    if ctx:
        try:
            p = out / f"front_map_{D}.png"
            sub = (f"DeepState 快照 #{ctx['cur']['id']}（{_paris(ctx['cur']['_ts'])} 巴黎时间）"
                   f"  对比  #{ctx['last_prev']['id']}（{_paris(ctx['last_prev']['_ts'])}）"
                   f" ｜ 俄控面积 {ctx['occ']:,.0f} km²")
            render.front_map(p, title=f"俄乌前线每日变化图 — {D}（欧洲/巴黎）", subtitle=sub,
                             layers=ctx["cur_l"], layers_prev7=ctx["l7"], chg=ctx["latest"],
                             chg7=ctx["w7"], secs=ctx["secs"],
                             attribution="来源：DeepStateMap (deepstatemap.live) 公开历史快照；边界 Natural Earth（乌克兰视角）；"
                                         "地名 GeoNames (CC BY 4.0)。公开 OSINT 资料，非情报；地图可能滞后/回溯修订。")
            files["map"] = p
            log.ok("渲染地图", str(p))
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            log.fail("渲染地图", str(e))
    try:
        import pandas as pd
        rows = [metrics[k] for k in sorted(metrics) if k <= D.isoformat()]
        start = (D - timedelta(days=max(a.backfill, 30))).isoformat()
        df = pd.DataFrame([r for r in rows if r["date"] >= start])
        if len(df):
            for c in ["occupied_km2", "grey_km2", "gained_km2", "lost_km2", "net_km2"]:
                df[c] = pd.to_numeric(df[c])
            p = out / f"timeline_{D}.png"
            render.timeline(p, df, f"DeepState 俄控面积与每日变化（{df.date.min()} 至 {df.date.max()}）")
            files["timeline"] = p
            df.to_csv(out / f"metrics_window_{D}.csv", index=False)
            files["metrics"] = out / f"metrics_window_{D}.csv"
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        log.fail("时间线图", str(e))
    if ctx:
        write_geojson(out / f"changes_{D}.geojson", ctx)
        files["geojson"] = out / f"changes_{D}.geojson"
    rep = out / f"report_{D}.md"
    rep.write_text(report(D, now, ctx, isw_items, heads, log, files), encoding="utf-8")
    files["report"] = rep
    summary = {"date": D.isoformat(), "generated_utc": now.isoformat(),
               "files": {k: str(v) for k, v in files.items()},
               "sources": [{"level": l, "source": s, "msg": m} for l, s, m in log.items]}
    if ctx:
        summary["numbers"] = ctx["numbers"]
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def analyse(history, D, log):
    cur = deepstate.snapshot_for_day(history, D)
    if cur is None:
        return {}
    cur_l = layers_for(cur["id"], log)
    idx = next(i for i, h in enumerate(history) if h["id"] == cur["id"])
    last_prev = history[idx - 1]
    lp_l = layers_for(last_prev["id"], log)
    numbers = {"snapshot_id": cur["id"], "snapshot_time_paris": _paris(cur["_ts"]),
               "occupied_km2": round(geo.area_km2(cur_l["occupied"]), 2),
               "grey_km2": round(geo.area_km2(cur_l["grey"]), 2)}
    periods = {}
    for n in (1, 7, 30):
        s = deepstate.snapshot_for_day(history, D - timedelta(days=n))
        if s is None:
            continue
        l_ = layers_for(s["id"], log)
        b = change_bundle(l_, cur_l, f"{n}天")
        b["from"] = s
        b["grey_change"] = numbers["grey_km2"] - geo.area_km2(l_["grey"])
        periods[n] = (b, l_)
        numbers[f"{n}d"] = {"from_snapshot": s["id"], "from_time_paris": _paris(s["_ts"]),
                            "gained_km2": round(b["gain_km2"], 2), "lost_km2": round(b["loss_km2"], 2),
                            "net_km2": round(b["gain_km2"] - b["loss_km2"], 2),
                            "grey_change_km2": round(b["grey_change"], 2)}
    latest = change_bundle(lp_l, cur_l, f"最近一次更新 {_paris(cur['_ts'])[5:10]}")
    numbers["latest_update"] = {"from_snapshot": last_prev["id"], "from_time_paris": _paris(last_prev["_ts"]),
                                "gained_km2": round(latest["gain_km2"], 2),
                                "lost_km2": round(latest["loss_km2"], 2),
                                "net_km2": round(latest["gain_km2"] - latest["loss_km2"], 2)}
    w7, l7 = periods.get(7, (change_bundle(lp_l, cur_l, "7天"), lp_l))
    secs = geo.sectors(w7["rows"])
    secs_latest = geo.sectors(latest["rows"])
    notes = []
    for h in history:
        if h["_ts"] > cur["_ts"] - timedelta(days=7) and h["_ts"] <= cur["_ts"]:
            t, places = deepstate.describe(h)
            notes.append((h, t, places))
    log.ok("DeepState 几何分析", f"快照 {cur['id']} 俄控 {numbers['occupied_km2']:,.1f} km²")
    return {"cur": cur, "cur_l": cur_l, "last_prev": last_prev, "latest": latest, "periods": periods,
            "w7": w7, "l7": l7, "secs": secs, "secs_latest": secs_latest, "notes": notes,
            "occ": numbers["occupied_km2"], "numbers": numbers}


def write_geojson(path, ctx):
    feats = []
    for tag, b in (("latest", ctx["latest"]), ("7d", ctx["w7"])):
        for r in b["rows"]:
            feats.append({"type": "Feature", "geometry": mapping(r["geometry"]),
                          "properties": {"period": tag, "kind": r["kind"], "area_km2": round(r["area_km2"], 3),
                                         "place": r["place"], "note": r["note"]}})
    path.write_text(json.dumps({"type": "FeatureCollection", "features": feats}, ensure_ascii=False), encoding="utf-8")


def _ds_link(lat, lon, z=14):
    return f"https://deepstatemap.live/en#{z}/{lat:.5f}/{lon:.5f}"


def report(D, now, ctx, isw_items, heads, log, files):
    L = [f"# 俄乌前线每日简报 — {D}（欧洲/巴黎时间）", "",
         f"_生成时间：{now.astimezone(TZ).strftime('%Y-%m-%d %H:%M')}（欧洲/巴黎）_", "",
         "> **说明**：本报告完全基于公开来源（OSINT）自动生成：DeepStateMap 公开地图快照、ISW 每日评估、公开新闻 RSS。"
         "它**不是情报产品**，不代表任何一方官方立场。DeepState 地图通常滞后于实际战况 1–3 天，且会事后修订；"
         "“俄方新增/乌方收复”仅指 DeepState 地图上“俄控区”多边形的变化，可能包含绘图修正。", ""]
    if not ctx:
        L += ["## ⚠️ 前线几何数据不可用", "", "DeepState 历史接口与本地缓存都不可用，今天无法计算面积变化。", ""]
    else:
        n, cur = ctx["numbers"], ctx["cur"]
        upd_today = cur["_ts"].astimezone(TZ).date() == D
        L += ["## 1. 面积变化（DeepState 俄控区，EPSG:6933 等积投影）", "",
              f"- 使用快照：#{cur['id']}，发布于 {_paris(cur['_ts'])}（巴黎）"
              + ("" if upd_today else f" —— **{D} 当天 DeepState 尚无新快照，以下“1天”为日历日对比**"),
              f"- 当前俄控面积：**{n['occupied_km2']:,.1f} km²**；灰色区（状态不明）：{n['grey_km2']:,.1f} km²", "",
              "| 时段 | 对比快照（巴黎时间） | 俄方新增 km² | 俄控减少 km² | 净变化 km² | 灰色区变化 km² |",
              "|---|---|---:|---:|---:|---:|"]
        lu = n["latest_update"]
        L.append(f"| 最近一次更新 | #{lu['from_snapshot']}（{lu['from_time_paris']}） | {fmt(lu['gained_km2'], False)} | "
                 f"{fmt(lu['lost_km2'], False)} | **{fmt(lu['net_km2'])}** | — |")
        for k in (1, 7, 30):
            if f"{k}d" in n:
                x = n[f"{k}d"]
                L.append(f"| {k} 天 | #{x['from_snapshot']}（{x['from_time_paris']}） | {fmt(x['gained_km2'], False)} | "
                         f"{fmt(x['lost_km2'], False)} | **{fmt(x['net_km2'])}** | {fmt(x['grey_change_km2'])} |")
        L += ["", "净变化为正表示俄控面积增加。", ""]

        L += [f"## 2. 最近一次更新的具体变化（#{ctx['last_prev']['id']} → #{cur['id']}）", ""]
        rows = ctx["latest"]["rows"][:12]
        if rows:
            L += ["| # | 类型 | 面积 km² | 位置（最近居民点，GeoNames） | 备注 | 地图 |", "|---|---|---:|---|---|---|"]
            for i, r in enumerate(rows, 1):
                L.append(f"| {i} | {'🔴 俄方新增' if r['kind'] == 'gain' else '🔵 俄控减少'} | {r['area_km2']:.2f} | "
                         f"{r['place']} | {r['note']} | [DeepState]({_ds_link(r['lat'], r['lon'])}) |")
        else:
            L.append("无大于 0.05 km² 的变化。")
        L += ["", "## 3. 热点方向（近 7 天变化，15 km 内聚类）", ""]
        if ctx["secs"]:
            L += ["| 方向（最近≥3000人城镇） | 州 | 俄方新增 km² | 俄控减少 km² | 净 km² | 变化块数 |", "|---|---|---:|---:|---:|---:|"]
            for s in ctx["secs"][:8]:
                L.append(f"| {s['name']} | {s['oblast']} | {s['gain']:.2f} | {s['loss']:.2f} | {s['net']:+.2f} | {s['n']} |")
        else:
            L.append("近 7 天无可识别变化。")
        L += ["", "### DeepState 官方更新日志（近 7 天，逐字引用其英文版说明）", ""]
        for h, t, places in reversed(ctx["notes"]):
            pl = "、".join(f"[{nm}]({u})" for nm, u in places) if places else ""
            L.append(f"- {_paris(h['_ts'])}：{t}" + (f"（链接地点：{pl}）" if pl else ""))
        L.append("")
    L += ["## 4. ISW《俄罗斯攻势评估》要点（英文原文引用）", ""]
    if isw_items:
        for it in isw_items[:2]:
            L.append(f"### [{it['title']}]({it['url']})")
            if it["asof"]:
                L.append(f"_{it['asof']}_")
            L.append("")
            if it["takeaways"]:
                L += [f"- {t}" for t in it["takeaways"]]
            else:
                L.append("（仅获取到标题；要点未能抓取）")
            L.append("")
        L.append("_ISW 官网 (understandingwar.org) 对脚本访问启用了 Cloudflare 验证，本程序读取的是其合作方 "
                 "AEI Critical Threats Project 在 criticalthreats.org 上同步发布的同一报告。要点为原文引用，未翻译以免失真。_")
    else:
        L.append("ISW 报告今日未能获取。")
    L += ["", "## 5. 新闻标题（公开 RSS，近 36 小时，原标题）", ""]
    if heads:
        for h in heads:
            ts = h["ts"].astimezone(TZ).strftime("%m-%d %H:%M") if h["ts"] else ""
            L.append(f"- [{h['title']}]({h['url']}) — {h['source']} {ts}")
    else:
        L.append("未获取到新闻标题。")
    L += ["", "## 6. 输出文件", ""]
    for k, v in files.items():
        L.append(f"- {k}: `{v}`")
    L += ["", "## 7. 数据源状态", ""]
    icon = {"OK": "✅", "WARN": "⚠️", "FAIL": "❌"}
    for lvl, s, m in log.items:
        L.append(f"- {icon[lvl]} **{s}**：{m}")
    L += ["", "## 8. 方法与局限", "",
          "- 俄控区 = DeepState 快照中标为 “Окуповано/Occupied” 的多边形 ∪ 克里米亚 ∪ 2014 年后占领区（ORDLO）∪ 图兹拉岛，"
          "并裁剪到 Natural Earth（乌克兰视角）国界 +1 km；“灰色区” = “状态不明”多边形（不计入俄控）。",
          "- 每个日历日（欧洲/巴黎时区）取当天结束前最后一个快照；DeepState 并非每天更新，未更新日的变化记为 0，变化会累积到下一次更新。",
          "- 面积在 EPSG:6933（等积）下计算；变化列表忽略 < 0.05 km² 的碎片（多为绘图修正），但净变化数字包含全部差异。",
          "- 地点名称为距变化块代表点最近的 GeoNames 居民点，仅用于定位，不代表该居民点易手。",
          "- 不同 OSINT 地图（DeepState、ISW、各方博主）口径不同，数值不可直接互比；本程序不做任何推测或模拟。", ""]
    return "\n".join(L)


if __name__ == "__main__":
    sys.exit(main())
