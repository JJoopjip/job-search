# job-search

A small pipeline that turns job-alert emails into a **graded shortlist you can review on your phone**. It finds
postings, grades each one by how likely it is to lead to an interview, and hands the promising ones to a
resume generator. Everything is semi-automated: it never applies for you. You review and submit every application.

> The data in this repo is fictional. The real career file, alert emails and decisions are git-ignored.

## What it does

1. **Collects jobs** from two places:
   - LinkedIn / Indeed alert emails, read from one Gmail label with a read-only scope (`alerts.py`).
   - Public job-board APIs: Greenhouse, Lever, Ashby, Himalayas (`fetch.py`, boards listed in `sources.json`).
2. **Pre-filters** by age (30 days), seniority and duplicates (`prefilter.py`). Alert jobs are never dropped.
3. **Grades** each job A–F against a written rubric ([RUBRIC.md](RUBRIC.md)), with a three-line "why".
4. **Serves a mobile page** (`server.py` + `site/index.html`) with filters (source, market, verdict), tabs
   (To review / Interested / Applied / Archived / Excluded) and a job-description viewer.
5. **One-click resume** (optional): calls a separate resume generator CLI for a tailored resume and cover letter.

## Grades

| Grade | Meaning | Verdict |
|---|---|---|
| A | Strong match | Apply |
| B | Good match | Apply (B- or better) |
| C | Partial or stretch | Stretch |
| D / F | Weak / off-target | Skip |

The grade means "chance of an interview". Skills match is the main driver; seniority is penalised in both
directions; health/pharma adds a bonus; hard blockers (language, work eligibility, clearance) move a job to an
**Excluded** tab. Full details: [RUBRIC.md](RUBRIC.md).

## Try the demo (no accounts needed)

```bash
python3 make_sample.py                      # writes fictional jobs to sample_data/
JOBS_DATA_DIR=sample_data JOBS_MAX_AGE_DAYS=36500 python3 server.py 5100
# open http://127.0.0.1:5100/
```

Standard-library Python 3 only for the demo. The resume buttons need the separate resume-generator CLI and will
not work in the demo.

## Run it on your own data

1. Create a Gmail label (e.g. "Job Alerts") and a filter that labels only your alert senders
   (`jobalerts-noreply@linkedin.com`, `donotreply@jobalert.indeed.com`).
2. `pip install google-api-python-client google-auth-oauthlib`, put a Google OAuth `credentials.json` in this folder (git-ignored), then `python3 alerts.py --selftest`
   and `python3 alerts.py --run --days 30`. Run `python3 alerts.py --auth` once (scope `gmail.modify`, own `token.json`).
   `--run` reads; `--trash` later moves an email to Trash only after all its jobs are on the board.
3. `python3 fetch.py` and `python3 prefilter.py`, then screen the shortlist and merge with `merge_screening.py`.
3b. Daily automation: `run_daily.sh` (cron) imports alerts, grades new jobs with a headless Claude run
   (`grade_daily.py`: fast model, 40 jobs/day cap, spend cap per batch), then trashes fully-listed alert emails.
4. `python3 server.py 5100`. The server binds to localhost only; expose it privately (for example with
   Tailscale) if you want it on your phone.

## Safety choices

- **One label only.** Mail is read, and alert emails are moved to Trash (recoverable for 30 days, never permanently
  deleted) only after their jobs are on the board. Nothing is sent. Unparseable mail is left alone.
- **No auto-apply** and no scraping of LinkedIn pages; alert emails and public APIs only.
- Mutating endpoints need a custom header and a same-host origin, run one job at a time, and have a daily cap.
- Private files are git-ignored: `credentials.json`, `token.json`, `master.yaml`, `data/`.

## Files

| File | Purpose |
|---|---|
| `alerts.py` | Gmail alert reader and parser |
| `fetch.py`, `sources.json` | Public job-board fetcher and board list |
| `prefilter.py` | Age, seniority and duplicate filter, builds the shortlist |
| `merge_screening.py` | Merges graded results into the screening file |
| `server.py`, `site/` | Local web server and mobile page |
| `RUBRIC.md` | Grading rubric |
| `master.example.yaml` | Fictional career file showing the schema |
| `make_sample.py` | Generates the demo data |
