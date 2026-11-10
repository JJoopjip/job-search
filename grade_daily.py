#!/usr/bin/env python3
"""Grade the not-yet-screened shortlist with a headless Claude run, then merge into data/screening.json.

Safety / cost: model is a fast one (GRADE_MODEL, default "sonnet"), at most GRADE_CAP jobs per day, batches of 15,
--max-budget-usd per batch. Claude may only Read and Write files (acceptEdits keeps writes inside this project folder). No shell, no web.
Nothing to grade -> exits without calling Claude (no cost).
"""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.resolve()
D = ROOT / "data"
MODEL = os.environ.get("GRADE_MODEL", "sonnet")
CAP = int(os.environ.get("GRADE_CAP", "40"))
BATCH = 15
BUDGET = os.environ.get("GRADE_BUDGET_USD", "2")
KEEP = ("url", "company", "title", "location", "market", "posted", "via", "source", "desc", "desc_truncated",
        "workplace", "industry", "eligibility", "seniority", "age", "title_match")

PROMPT = """You are grading job postings for one candidate. Work only with files; do not browse.
1. Read {root}/RUBRIC.md (grading rules) and {root}/master.yaml (the candidate's facts: never invent or assume facts).
2. Read {root}/data/grade_example.json (the exact output shape of one graded job).
3. Read {root}/data/grade_in.json (jobs to grade).
4. Write {root}/data/screening_new_daily_{n}.json as {{"jobs":[...]}} with exactly one graded entry per input job, same
   order. Copy url, company, title, location, market, posted and via from the input verbatim. Follow the example's fields
   (grade A-F with +/-, verdict, one_line, jd_summary, requirements_seen, coverage, gaps, eligibility, eligibility_note,
   profile, tailor_with, why{{level,skills,domain}}). Verdict: apply = B- or better with no blocker, stretch = C+ to C,
   skip = below or blocked. Jobs with title_only evidence (alert cards) are graded from title/company/location and say so.
   If a job is blocked by a hard eligibility blocker, set eligibility to "blocked" with the reason.
Write valid JSON only in that file. Do not write anywhere else. Reply with one line: the number of jobs graded."""


def main():
    sl = json.loads((D / "shortlist.json").read_text()).get("jobs", [])[:CAP]
    if not sl:
        print("grade_daily: nothing to grade")
        return 0
    if not (ROOT / "master.yaml").exists():
        print("grade_daily: master.yaml missing")
        return 1
    scr = json.loads((D / "screening.json").read_text())["jobs"]
    ex = next((j for j in scr if j.get("gaps") and j.get("coverage") and j.get("why")), scr[0])
    (D / "grade_example.json").write_text(json.dumps(ex, indent=1))
    total = 0
    for n, i in enumerate(range(0, len(sl), BATCH)):
        batch = [{k: j[k] for k in KEEP if k in j} for j in sl[i:i + BATCH]]
        # long descriptions are trimmed; the grader needs requirements, not the whole posting
        for j in batch:
            j["desc"] = (j.get("desc") or "")[:3500]
        (D / "grade_in.json").write_text(json.dumps({"jobs": batch}, indent=1))
        out = D / f"screening_new_daily_{n}.json"
        out.unlink(missing_ok=True)
        cmd = ["claude", "-p", PROMPT.format(root=ROOT, n=n), "--model", MODEL, "--permission-mode", "acceptEdits",
               "--allowedTools", "Read", "Write", "--disallowedTools", "Bash", "WebFetch", "WebSearch",
               "--max-budget-usd", BUDGET, "--no-session-persistence"]
        r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=1500)
        print(f"batch {n}: exit {r.returncode}; {r.stdout.strip()[:120]}")
        try:
            got = json.loads(out.read_text())["jobs"]
            ok = len(got) == len(batch) and all(g.get("url") for g in got)
        except (OSError, ValueError, KeyError):
            ok = False
        if not ok:
            print(f"batch {n}: output missing or invalid; skipped (will retry tomorrow)")
            out.unlink(missing_ok=True)
            continue
        total += len(got)
    if total:
        subprocess.run([sys.executable, str(ROOT / "merge_screening.py")], cwd=str(ROOT), check=False)
    for f in D.glob("screening_new_daily_*.json"):
        f.unlink()
    (D / "grade_in.json").unlink(missing_ok=True)
    print(f"grade_daily: graded {total} of {len(sl)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
