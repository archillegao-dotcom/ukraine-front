"""Collect the read-only seed data bundled into the packaged app (PyInstaller).

    python tools/make_seed.py [DEST] [--days 14] [--fetch]

Copies from ./data into DEST (default build/seed_data) only what the app needs to start
offline: daily metrics, static geodata, ISW 2022 layer, Kiel aid tables, small caches and
the DeepState snapshots of the last --days days. Raw/rebuildable data (aid_raw/*.xlsx,
layers_v*/diffs_v* pickles, polnews cache, logs, settings) is skipped.
--fetch downloads missing recent snapshots from DeepState (used in CI, where data/snapshots
is not in git); failures are only warnings — the app downloads them on first start anyway.
"""
import argparse
import csv
import shutil
import sys
import time
from datetime import date, timedelta
from pathlib import Path

R = Path(__file__).resolve().parent.parent
SRC = R / "data"

KEEP = [
    "daily_metrics.csv",
    "static/ne_10m_admin_0_countries_ukr.zip",
    "static/UA.zip",
    "static/admin1CodesASCII.txt",
    "static/deepstate_baseline_crimea_ordlo.geojson",
    "static/isw_cot_2022-02-24_2022-04-02.geojson.gz",
    "aid/aid_events.csv.gz",
    "aid/news_events.csv.gz",
    "aid/aid_meta.json",
    "cache/deepstate_history.json",
]
KEEP_DIRS = ["cache/isw", "cache/news"]
REQUIRED = {"daily_metrics.csv", "static/ne_10m_admin_0_countries_ukr.zip", "static/UA.zip",
            "static/admin1CodesASCII.txt", "static/isw_cot_2022-02-24_2022-04-02.geojson.gz"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dest", nargs="?", default=str(R / "build" / "seed_data"))
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--fetch", action="store_true")
    a = ap.parse_args()
    dest = Path(a.dest)
    if dest.exists():
        shutil.rmtree(dest)
    missing = []
    for rel in KEEP:
        p = SRC / rel
        if not p.exists():
            missing.append(rel)
            continue
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, dest / rel)
    for rel in KEEP_DIRS:
        if (SRC / rel).is_dir():
            shutil.copytree(SRC / rel, dest / rel)
    bad = sorted(REQUIRED & set(missing))
    if bad:
        sys.exit(f"[make_seed] required seed files missing: {bad}")
    for m in missing:
        print(f"[make_seed] optional file missing (skipped): {m}")

    rows = list(csv.DictReader(open(SRC / "daily_metrics.csv", encoding="utf-8")))
    last = max(r["date"] for r in rows)
    cut = (date.fromisoformat(last) - timedelta(days=a.days)).isoformat()
    ids = {r[k] for r in rows if r["date"] >= cut for k in ("snapshot_id", "prev_snapshot_id")}
    ids = sorted(int(i) for i in ids if i and int(i) > 0)   # negative ids = ISW layer, already bundled
    snap = dest / "snapshots"
    snap.mkdir(parents=True, exist_ok=True)
    got = 0
    # Snapshots are optional: the app downloads any that are missing on first launch.
    # Cap the network wait so a slow or blocked DeepState API cannot eat the CI job.
    fetch_deadline = time.time() + 180
    for sid in ids:
        p = SRC / "snapshots" / f"{sid}.geojson.gz"
        if p.exists():
            shutil.copy2(p, snap / p.name)
            got += 1
        elif a.fetch:
            if time.time() > fetch_deadline:
                print("[make_seed] warning: snapshot fetch budget (180s) reached; "
                      "the rest download on first launch")
                break
            try:
                sys.path.insert(0, str(R))
                from ukraine_front import config, net
                data = net.get(config.DEEPSTATE_GEOJSON.format(id=sid), timeout=20, retries=1).json()
                net.write_json_gz(snap / p.name, data)
                got += 1
            except Exception as e:  # noqa: BLE001
                print(f"[make_seed] warning: snapshot {sid} not fetched: {e}")
    size = sum(f.stat().st_size for f in dest.rglob("*") if f.is_file())
    print(f"[make_seed] {dest}: {got}/{len(ids)} recent snapshots, total {size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
