#!/usr/bin/env python3
"""Run fetch.py, but keep the previous data/jobs.json if the new fetch looks broken (fewer than 60% of the old jobs).

The board hides postings that are missing from jobs.json as "taken down", so a half-failed fetch must not replace a good one.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.resolve()
cur, prev = ROOT / "data" / "jobs.json", ROOT / "data" / "jobs.prev.json"


def count(p):
    try:
        return len(json.loads(p.read_text())["jobs"])
    except (OSError, ValueError, KeyError):
        return 0


old = count(cur)
if old:
    shutil.copy(cur, prev)
r = subprocess.run([sys.executable, str(ROOT / "fetch.py")], cwd=str(ROOT))
new = count(cur)
if r.returncode != 0 or (old and new < 0.6 * old):
    if old:
        shutil.copy(prev, cur)
    print(f"fetch_safe: kept previous jobs.json (old {old}, new {new}, exit {r.returncode})")
    sys.exit(1)
print(f"fetch_safe: ok, {new} jobs (was {old})")
