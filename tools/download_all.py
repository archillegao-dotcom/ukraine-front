"""Download every DeepState snapshot listed in the public history (cached, resumable)."""
import json, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ukraine_front import deepstate
from ukraine_front.config import CACHE, SNAPSHOTS

h = json.loads((CACHE / "deepstate_history.json").read_text(encoding="utf-8"))
todo = [e["id"] for e in h if not (SNAPSHOTS / f"{e['id']}.geojson.gz").exists()]
print("to download:", len(todo), flush=True)
ok = fail = 0
def job(i):
    for k in range(3):
        try:
            deepstate.fetch_snapshot(i); return True
        except Exception as e:  # noqa: BLE001
            err = e; time.sleep(3)
    print("FAIL", i, err, flush=True); return False
with ThreadPoolExecutor(6) as ex:
    for n, f in enumerate(as_completed([ex.submit(job, i) for i in todo])):
        ok += f.result(); fail += (not f.result())
        if n % 100 == 0: print(n, ok, fail, flush=True)
print("done", ok, fail)
