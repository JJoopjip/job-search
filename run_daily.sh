#!/usr/bin/env bash
# Daily job-alert run (cron). Imports new alert emails, then trashes the ones whose jobs are already on the board.
cd "$(dirname "$0")" || exit 1
PY=/home/joopjip/application_tracker/.venv/bin/python
mkdir -p data
[ -f token.json ] || { echo "$(date '+%F %T') no token.json yet: run alerts.py --auth once" >> data/daily.log; exit 0; }
{
  echo "=== $(date '+%F %T') ==="
  export PATH="$HOME/.local/bin:$PATH"
  $PY alerts.py --run --days 14 && python3 prefilter.py && python3 grade_daily.py
  $PY alerts.py --trash --days 45   # runs even if grading failed: it only trashes emails whose jobs are on the board
} >> data/daily.log 2>&1
