#!/usr/bin/env python3
"""Merge data/screening_new*.json into data/screening.json (dedupe by url+title+location, validate)."""
import glob
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
(D / "screening.json").write_text(json.dumps(old, indent=1))
print(f"added {added}, invalid {bad}, total {len(old['jobs'])}")
