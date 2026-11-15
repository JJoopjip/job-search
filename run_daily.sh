#!/usr/bin/env bash
# Daily run (cron): fetch career sites, import alert emails, grade new jobs, then trash fully-listed alert emails.
cd "$(dirname "$0")" || exit 1
PY=/home/joopjip/application_tracker/.venv/bin/python
export PATH="$HOME/.local/bin:$PATH"
mkdir -p data
exec 9>data/.run.lock
flock -n 9 || { echo "$(date '+%F %T') previous run still going; skipping" >> data/daily.log; exit 0; }  # a long grading backlog must not overlap the next run
{
  echo "=== $(date '+%F %T') ==="
  python3 fetch_safe.py                      # career-site boards -> data/jobs.json (keeps old file if the fetch looks broken)
  if [ -f token.json ]; then
    $PY alerts.py --run --days 14            # alert emails -> data/alert_jobs.json
  else
    echo "no token.json yet: run alerts.py --auth once (alert emails skipped)"
  fi
  python3 prefilter.py && python3 grade_daily.py
  [ -f token.json ] && $PY alerts.py --trash --days 45   # only trashes emails whose jobs are already on the board
} >> data/daily.log 2>&1
