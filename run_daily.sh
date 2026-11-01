#!/usr/bin/env bash
# Command-line daily report: ./run_daily.sh [--date YYYY-MM-DD] [--backfill 30] [--no-news]
set -eu
cd "$(dirname "$0")"
exec .venv/bin/python -m ukraine_front.daily ${1+"$@"}
