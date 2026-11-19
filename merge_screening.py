#!/usr/bin/env python3
"""Merge data/screening_new*.json into data/screening.json (dedupe by url+title+location, validate)."""
import datetime
import glob
import hashlib
import json
import shutil
from pathlib import Path

D = Path(__file__).parent / "data"
shutil.copy(D / "screening.json", D / "screening.prev.json")
old = json.loads((D / "screening.json").read_text())
need = {"url", "company", "title", "location", "market", "verdict", "one_line"}
k = lambda j: (j["url"], j["title"].lower(), j["location"].lower())
have = {k(j) for j in old["jobs"]}
added = bad = 0
for f in sorted(glob.glob(str(D / "screening_new*.json"))):
    for j in json.loads(Path(f).read_text())["jobs"]:
        if not need <= set(j) or not str(j["url"]).startswith("https://") or j["verdict"] not in ("apply", "stretch", "skip"):
            bad += 1
            continue
        if k(j) in have:
            continue
        have.add(k(j))
        old["jobs"].append(j)
        added += 1

# Below C: not shown and never applied to, so keep only a tiny "already graded" record (url/title/location/grade)
# in data/low_grades.json (prefilter and alerts.py treat it like screening.json) and drop the heavy entry.
KEEP_GRADES = {"A+", "A", "A-", "B+", "B", "B-", "C+", "C"}
jid = lambda j: hashlib.sha1((j["url"] + "\n" + j["title"].lower().strip()).encode()).hexdigest()[:16]
try:
    marked = set(json.loads((D / "decisions.json").read_text()))  # jobs you marked stay on the board
except (OSError, ValueError):
    marked = set()
try:
    low = json.loads((D / "low_grades.json").read_text())
except (OSError, ValueError):
    low = {"jobs": []}
low_have = {k(j) for j in low["jobs"]}
kept, moved, today = [], 0, datetime.date.today().isoformat()
for j in old["jobs"]:
    if str(j.get("grade", "")).strip() in KEEP_GRADES or jid(j) in marked:
        kept.append(j)
        continue
    if k(j) not in low_have:
        low["jobs"].append({"url": j["url"], "title": j["title"], "location": j["location"], "grade": j.get("grade"), "graded": today})
        low_have.add(k(j))
    moved += 1
old["jobs"] = kept
(D / "low_grades.json").write_text(json.dumps(low, indent=1))
(D / "screening.json").write_text(json.dumps(old, indent=1))
print(f"added {added}, invalid {bad}, below C archived {moved}, total {len(old['jobs'])}")
