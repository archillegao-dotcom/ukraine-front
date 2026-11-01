import gzip
import json
import time
import requests
from .config import UA

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})


def get(url, timeout=40, retries=3, **kw):
    last = None
    for i in range(retries):
        try:
            r = SESSION.get(url, timeout=timeout, **kw)
            if r.status_code == 200:
                return r
            last = RuntimeError(f"HTTP {r.status_code} for {url}")
            if r.status_code in (401, 403, 404):
                break
        except requests.RequestException as e:  # network error
            last = e
        time.sleep(2 * (i + 1))
    raise last


def write_json_gz(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    tmp.replace(path)


def read_json_gz(path):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)
