#!/usr/bin/env bash
# Ukraine front OSINT tracker - one-click launcher for macOS / Linux.
# 俄乌前线 OSINT 追踪器 - macOS / Linux 一键启动。
# First run creates .venv and installs dependencies (needs internet, ~1-3 min).
# Compatible with macOS /bin/bash 3.2: every variable is written as ${VAR}
# (a multibyte character right after $VAR breaks bash 3.2), and "$@" is
# written as ${1+"$@"} (bash < 4 treats an empty "$@" as unbound under set -u).
set -eu
cd "$(dirname "$0")"
PORT="${UF_PORT:-8765}"

PY=""
for c in python3.13 python3.12 python3.11 python3.10 python3 python; do
  if command -v "${c}" >/dev/null 2>&1 && "${c}" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' >/dev/null 2>&1; then
    PY="${c}"
    break
  fi
done
if [ -z "${PY}" ]; then
  echo "[!] Python 3.10+ not found. / 未找到 Python 3.10 或更高版本。"
  echo "    Install from https://www.python.org/downloads/ (macOS: brew install python)"
  exit 1
fi

if [ ! -x ".venv/bin/python" ]; then
  echo ">> Creating virtual environment .venv with ${PY} / 正在创建虚拟环境"
  "${PY}" -m venv .venv
fi
STAMP=".venv/.req_stamp"
if [ ! -f "${STAMP}" ] || ! cmp -s requirements.txt "${STAMP}"; then
  echo ">> Installing dependencies (first run is slow) / 正在安装依赖（首次较慢）"
  .venv/bin/python -m pip install --upgrade pip >/dev/null
  .venv/bin/python -m pip install -r requirements.txt
  cp requirements.txt "${STAMP}"
fi
echo ">> Starting http://localhost:${PORT}/ (Ctrl+C to quit) / 正在启动，按 Ctrl+C 退出"
exec .venv/bin/python -m ukraine_front.app --port "${PORT}" ${1+"$@"}
