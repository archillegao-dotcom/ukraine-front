"""Build /workspace/ukraine_front_app.zip (no venv, no big caches; keeps exec bits)."""
import csv, stat, sys, zipfile
from datetime import date, timedelta
from pathlib import Path
R = Path(__file__).resolve().parent.parent
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else R.parent / "ukraine_front_app_mac_win.zip"
rows = list(csv.DictReader(open(R / "data/daily_metrics.csv", encoding="utf-8")))
last = max(r["date"] for r in rows)
cut = (date.fromisoformat(last) - timedelta(days=14)).isoformat()
ids = {r["snapshot_id"] for r in rows if r["date"] >= cut} | {r["prev_snapshot_id"] for r in rows if r["date"] >= cut}
EXEC = {".sh", ".command"}
n = 0
with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
    for p in sorted(R.rglob("*")):
        if p.is_dir():
            continue
        rel = p.relative_to(R); parts = rel.parts
        if parts[0] in (".venv", "screenshots") or "__pycache__" in parts or p.suffix in (".tmp", ".pyc"):
            continue
        if parts[0] == "data":
            if parts[1].startswith(("layers", "diffs")) or parts[1] in ("logs", "aid_raw"):
                continue
            if parts[1] == "cache" and len(parts) > 2 and parts[2] == "polnews":
                continue
            if p.name in ("settings.json", "scheduler_state.json"):
                continue
            if parts[1] == "static" and p.name in ("UA.txt", "readme.txt"):
                continue
            if parts[1] == "snapshots" and p.name.split(".")[0] not in ids:
                continue
        if parts[0] == "output" and len(parts) > 1 and parts[1] != last:
            continue
        zi = zipfile.ZipInfo.from_file(p, "ukraine_front_app/" + rel.as_posix())
        mode = 0o755 if p.suffix in EXEC else 0o644
        zi.external_attr = (stat.S_IFREG | mode) << 16
        zi.compress_type = zipfile.ZIP_DEFLATED
        z.writestr(zi, p.read_bytes()); n += 1
print(OUT, n, "files", round(OUT.stat().st_size / 1e6, 1), "MB")
