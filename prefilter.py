#!/usr/bin/env python3
"""Cheap rule-based pre-filter: data/jobs.json -> data/shortlist.json (jobs worth an AI screening pass).

Drops: already-screened jobs, senior-level titles/seniority, duplicates.
Keeps: all health-industry matches, plus a capped number of strong-title jobs from other industries.
Usage: python3 prefilter.py [--cap N] [--other-cap N]
"""
import argparse
import datetime
import json
import re
from pathlib import Path

ROOT = Path(__file__).parent
MAX_AGE_DAYS = 30
CUTOFF = (datetime.date.today() - datetime.timedelta(days=MAX_AGE_DAYS)).isoformat()
SENIOR_RE = re.compile(r"\b(senior|sr\.?|lead|staff|principal|head)\b", re.I)
STRONG_OTHER = re.compile(r"(business development|partnership|project manager|program manager|project coordinator|"
                          r"implementation|market access|clinical)", re.I)


def key(j):
    return (j["url"], j["title"].lower().strip(), j["location"].lower().strip())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cap", type=int, default=40)
    ap.add_argument("--other-cap", type=int, default=30)
    a = ap.parse_args()
    jobs = json.loads((ROOT / "data/jobs.json").read_text())["jobs"]
    alerts = ROOT / "data/alert_jobs.json"  # from alerts.py (label-restricted Gmail reader), if present
    if alerts.exists():
        jobs += json.loads(alerts.read_text()).get("jobs", [])
    scr_path = ROOT / "data/screening.json"
    screened = set()
    if scr_path.exists():
        screened = {key(j) for j in json.loads(scr_path.read_text()).get("jobs", [])}

    stats = {"total": len(jobs), "already_screened": 0, "senior": 0, "duplicate": 0, "other_over_cap": 0}
    health, other, seen, alert_jobs = [], [], set(), []
    for j in jobs:
        k = key(j)
        if k in screened:
            stats["already_screened"] += 1
            continue
        if k in seen:
            stats["duplicate"] += 1
            continue
        seen.add(k)
        if j.get("posted") and j["posted"][:10] < CUTOFF:  # older than MAX_AGE_DAYS
            stats["older"] = stats.get("older", 0) + 1
            continue
        if j.get("source") == "alert":  # LinkedIn alert cards are never dropped (user choice): all get screened
            alert_jobs.append(j)
            continue
        sen = j.get("seniority", "")
        if SENIOR_RE.search(j["title"]) or ("Senior" in sen and "Mid-level" not in sen):
            stats["senior"] += 1
            continue
        (health if j["industry"] == "health" else other).append(j)

    # Toronto first, then remote; newest first
    def rank(j):
        return (0 if j.get("source") == "alert" else 1, 0 if j["market"] == "toronto" else 1, 0 if j["eligibility"] in ("ok", "canada-ok") else 1,
                "".join(chr(255 - ord(c)) for c in (j.get("posted") or "0000-00-00")))
    health.sort(key=rank)
    other = [j for j in other if STRONG_OTHER.search(j["title"]) and (not j.get("seniority") or "Mid-level" in j["seniority"])]
    other.sort(key=rank)
    stats["other_over_cap"] = max(0, len(other) - a.other_cap)
    short = alert_jobs + (health + other[:a.other_cap])[:a.cap]
    (ROOT / "data/shortlist.json").write_text(json.dumps({"stats": stats, "jobs": short}, indent=1))
    print(stats, "-> shortlist:", len(short), f"(alert cards {len(alert_jobs)}, other {len(short) - len(alert_jobs)})")


if __name__ == "__main__":
    main()
