"""Precompute layers for every snapshot (parallel) and daily metrics since 2022-02-24."""
import sys, time
from concurrent.futures import ProcessPoolExecutor
from datetime import date, timedelta
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _one(sid):
    from ukraine_front import core
    try:
        core.layers_for(sid)
        return sid, None
    except Exception as e:  # noqa: BLE001
        return sid, str(e)


if __name__ == "__main__":
    from ukraine_front import core, deepstate
    from ukraine_front.config import DATA, TZ
    from datetime import datetime

    class L:
        def ok(self, s, m): print("OK", s, m, flush=True)
        def warn(self, s, m): print("WARN", s, m, flush=True)
        def fail(self, s, m): print("FAIL", s, m, flush=True)
    log = L()
    h = core.load_full_history(log)
    D = datetime.now(TZ).date()
    start = date(2022, 2, 24)
    days = [start + timedelta(days=i) for i in range((D - start).days + 1)]
    ids = sorted({s["id"] for d in days if (s := deepstate.snapshot_for_day(h, d)) and s["id"] > 0})
    todo = [i for i in ids if not (DATA / f"layers_v{deepstate.LAYER_VERSION}" / f"{i}.pkl").exists()]
    print("snapshots used:", len(ids), "to build:", len(todo), flush=True)
    t = time.time()
    with ProcessPoolExecutor(int(sys.argv[1]) if len(sys.argv) > 1 else 3) as ex:
        for n, (sid, err) in enumerate(ex.map(_one, todo, chunksize=4)):
            if err:
                print("ERR", sid, err, flush=True)
            if n % 100 == 0:
                print("layers", n, round(time.time() - t), flush=True)
    print("metrics...", flush=True)
    core.update_metrics(h, days, log, progress=lambda i, n, d: print("metrics", i, n, d, flush=True) if i % 200 == 0 else None)
    print("done", round(time.time() - t), flush=True)
