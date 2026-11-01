#!/usr/bin/env bash
# Start the packaged app and query the local API.
# On macOS this unpacks the ditto zip first, so the test is the archive users download
# (symlinks + ad-hoc signature), and it launches with --no-terminal.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

RUNNER_OS="${RUNNER_OS:-}"
if [ -z "$RUNNER_OS" ]; then
  case "$(uname -s)" in
    Darwin) RUNNER_OS=macOS ;;
    MINGW*|MSYS*|CYGWIN*) RUNNER_OS=Windows ;;
    *) RUNNER_OS=Linux ;;
  esac
fi

WORKDIR="${RUNNER_TEMP:-/tmp}/uf-smoke"
rm -rf "$WORKDIR"
mkdir -p "$WORKDIR"
export UF_HOME="$WORKDIR/home"
export UF_REFRESH_MINUTES=0
export PYTHONUNBUFFERED=1
LOG="$WORKDIR/uf.log"

if [ "$RUNNER_OS" = "macOS" ]; then
  ZIP="$(echo dist/UkraineFront-macos-*.zip)"
  test -f "$ZIP"
  ditto -x -k "$ZIP" "$WORKDIR/unpacked"
  APP="$WORKDIR/unpacked/UkraineFront.app"
  BIN="$APP/Contents/MacOS/UkraineFront"
  test -x "$BIN"
  if ! find "$APP" -name daily_metrics.csv | grep -q .; then
    echo "seed data missing from the unpacked .app"
    find "$APP/Contents" -maxdepth 3 -type d -o -type l
    exit 1
  fi
  if ! find "$APP" -type l | grep -q .; then
    echo "unpacked .app has no symlinks; ditto did not preserve the bundle"
    exit 1
  fi
  echo "codesign of unpacked app:"
  codesign --verify --deep --verbose=2 "$APP"
  echo "LSMinimumSystemVersion=$(plutil -extract LSMinimumSystemVersion raw "$APP/Contents/Info.plist")"
else
  if [ "$RUNNER_OS" = "Windows" ]; then
    BIN="$ROOT/dist/UkraineFront/UkraineFront.exe"
  else
    BIN="$ROOT/dist/UkraineFront/UkraineFront"
  fi
  test -f "$BIN"
fi

"$BIN" --port 18765 --no-browser --no-terminal >"$LOG" 2>&1 </dev/null &
PID=$!

cleanup() {
  kill "$PID" 2>/dev/null || true
  if [ "$RUNNER_OS" = "Windows" ]; then
    taskkill //F //IM UkraineFront.exe >/dev/null 2>&1 || true
  fi
  echo "----- packaged app log -----"
  cat "$LOG" 2>/dev/null || true
}
trap cleanup EXIT

for _ in $(seq 1 90); do
  if curl -sf --max-time 5 http://127.0.0.1:18765/api/meta -o "$WORKDIR/meta.json"; then
    break
  fi
  if ! kill -0 "$PID" 2>/dev/null; then
    echo "packaged app exited before /api/meta responded"
    exit 1
  fi
  sleep 2
done
test -s "$WORKDIR/meta.json"

curl -sf --max-time 180 http://127.0.0.1:18765/ -o /dev/null
curl -sf --max-time 180 http://127.0.0.1:18765/api/borders -o /dev/null
curl -sf --max-time 60 "http://127.0.0.1:18765/api/aid/timeline" -o /dev/null
python -c "import json,sys; d=json.load(open(sys.argv[1],encoding='utf-8')); n=len(d['days']); print('days:', n); assert n > 1000" "$WORKDIR/meta.json"
test -f "$UF_HOME/data/daily_metrics.csv"
grep -F -- "--no-terminal: staying in this process" "$LOG"
echo "smoke test ok"
