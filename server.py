#!/usr/bin/env python3
"""Web server for the job screening page (Tailscale-only, mounted at /jobs).

Binds to 127.0.0.1 only. Serves site/ and data/screening.json (each job gets an id, has_jd and gen info injected).
Extra endpoints:
  GET  /api/jd?id=            full job description text (from data/jd_found.json or data/jobs.json)
  POST /api/generate?id=[&cover=1]   run `resume-gen <jd> --fast [--cover]` in the background (one run at a time)
  POST /api/cover?id=         add a cover letter to a resume this page already generated
  GET  /api/run?run=          status of a background run
  GET  /api/file?id=&file=    download resume/cover PDF or DOCX produced by a run started from this page
Guards: POST needs the X-Jobs header and a same-host Origin; only jobs listed in screening.json that have a
description can be run; one run at a time; daily cap; downloads limited to folders this server created.
It never submits an application and never contacts the Fieldnotes tracker.
"""
import hashlib
import http.server
import json
import mimetypes
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).parent.resolve()
SITE = ROOT / "site"
DATA = Path(os.environ.get("JOBS_DATA_DIR", ROOT / "data")).resolve()  # JOBS_DATA_DIR=sample_data for the demo
RUNS = DATA / "runs"
RESUME_ROOT = Path(os.environ.get("RESUME_GEN_ROOT", "/home/joopjip/resume_generator")).resolve()
RESUME_GEN = RESUME_ROOT / "resume-gen"
RESUME_OUT = RESUME_ROOT / "output"
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 5100
MAX_AGE_DAYS = int(os.environ.get("JOBS_MAX_AGE_DAYS", "30"))
DAILY_CAP = int(os.environ.get("JOBS_DAILY_CAP", "25"))
RUN_TIMEOUT = 25 * 60
FILES = {"resume.pdf": "application/pdf", "cover_letter.pdf": "application/pdf",
         "resume.docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
         "cover_letter.docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
SLUG_RE = re.compile(r"^[A-Za-z0-9._-]+$")
ID_RE = re.compile(r"^[0-9a-f]{16}$")

_lock = threading.Lock()          # guards runs/state files and the single-run slot
_runs = {}                        # run id -> status dict (in memory)
_busy = {"run": None}


def jid(url, title):
    return hashlib.sha1((url + "\n" + title.lower().strip()).encode()).hexdigest()[:16]


def load(name, default):
    try:
        return json.loads((DATA / name).read_text())
    except (OSError, ValueError):
        return default


def screened():
    return load("screening.json", {"jobs": []}).get("jobs", [])


def find_job(i):
    return next((j for j in screened() if jid(j["url"], j["title"]) == i), None)


def jd_for(job):
    """Full description text for a screened job, or ('', '') if we only have an alert snippet."""
    found = load("jd_found.json", {}).get("found", {}).get(job["url"])
    if found and len(found.get("text", "")) > 200:
        return found["text"], found.get("source_url", "")
    for j in load("jobs.json", {"jobs": []}).get("jobs", []):
        if j["url"] == job["url"] and j["title"].lower() == job["title"].lower() and \
                not j.get("desc_truncated") and len(j.get("desc", "")) > 400:
            return j["desc"], j["url"]
    return "", ""


def generated():
    return load("generated.json", {})


def save_generated(g):
    DATA.mkdir(exist_ok=True)
    tmp = DATA / "generated.json.tmp"
    tmp.write_text(json.dumps(g, indent=1))
    tmp.replace(DATA / "generated.json")


def source_of(j):
    """Where the job came from: linkedin / indeed / jobsdb alert emails, himalayas, or a company career site."""
    host = urlparse(j["url"]).netloc.lower()
    via = str(j.get("via", "")).lower()
    for name in ("linkedin", "indeed", "jobsdb", "himalayas"):
        if name in host or name in via:
            return name
    return "company"


def gen_coverage(slug):
    """Resume-generator keyword coverage for a finished run: (percent, confidence) from its coverage.md."""
    try:
        t = (RESUME_OUT / slug / "coverage.md").read_text()
    except (OSError, ValueError):
        return None
    m = re.search(r"\*\*(\d+)%\*\*", t)
    c = re.search(r"confidence \*\*(\w+)\*\*", t)
    return {"pct": int(m.group(1)) if m else None, "confidence": c.group(1) if c else None}


def runs_today():
    day = time.strftime("%Y-%m-%d")
    return load("runs_log.json", {}).get(day, 0)


def count_run():
    log = load("runs_log.json", {})
    day = time.strftime("%Y-%m-%d")
    log = {day: log.get(day, 0) + 1}
    (DATA / "runs_log.json").write_text(json.dumps(log))


def _dirs():
    return {p.name for p in RESUME_OUT.iterdir() if p.is_dir()} if RESUME_OUT.is_dir() else set()


def _exec(cmd, logf):
    with open(logf, "w") as lf:
        p = subprocess.Popen(cmd, cwd=str(RESUME_ROOT), stdout=lf, stderr=subprocess.STDOUT, text=True,
                             env=os.environ.copy(), stdin=subprocess.DEVNULL)
        try:
            return p.wait(timeout=RUN_TIMEOUT)
        except subprocess.TimeoutExpired:
            p.kill()
            return -9


def _worker(run, job_id, kind, text, slug):
    st = _runs[run]
    try:
        RUNS.mkdir(parents=True, exist_ok=True)
        logf = RUNS / f"{run}.log"
        if kind == "cover":
            code = _exec([str(RESUME_GEN), "cover-letter", f"output/{slug}", "--fast"], logf)
        else:
            jd = RUNS / f"jd-{job_id}.txt"
            jd.write_text(text + "\n")
            before = _dirs()
            cmd = [str(RESUME_GEN), str(jd), "--fast"] + (["--cover"] if kind == "resume+cover" else [])
            code = _exec(cmd, logf)
            new = sorted(_dirs() - before, key=lambda n: (RESUME_OUT / n).stat().st_mtime)
            slug = new[-1] if new else None
            try:
                jd.unlink()
            except OSError:
                pass
        folder = RESUME_OUT / slug if slug else None
        have = [f for f in FILES if folder and (folder / f).is_file()]
        if code == 0 and slug and ("resume.pdf" in have):
            with _lock:
                g = generated()
                g[job_id] = {"slug": slug, "files": have, "at": time.strftime("%Y-%m-%d %H:%M")}
                save_generated(g)
            st.update(state="done", files=have)
        else:
            st.update(state="error", error=f"The run did not finish cleanly (exit {code}). Try again, or run resume-gen by hand.")
    except Exception as e:  # noqa: BLE001 - report any launcher failure to the page
        st.update(state="error", error=str(e)[:200])
    finally:
        st["ended"] = time.time()
        _busy["run"] = None


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "jobs-screen/0.2"

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    def _send(self, code, body, ctype, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj).encode(), "application/json; charset=utf-8")

    def _route(self):
        u = urlparse(self.path)
        path = u.path
        if path == "/jobs" or path.startswith("/jobs/"):
            path = path[len("/jobs"):] or "/"
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        return path, q

    # ------------------------------------------------------------------ GET
    def do_GET(self):
        path, q = self._route()
        if path == "/api/jd":
            return self._api_jd(q)
        if path == "/api/run":
            st = _runs.get(q.get("run", ""))
            if not st:
                return self._json(404, {"error": "unknown run"})
            return self._json(200, {k: v for k, v in st.items() if k in ("state", "files", "error", "kind", "started")})
        if path == "/api/file":
            return self._api_file(q)
        if path in ("", "/"):
            path = "/index.html"
        name = path.lstrip("/")
        if name == "screening.json":
            return self._screening()
        if name == "jobs.json":
            f = DATA / "jobs.json"
        else:
            f = (SITE / name).resolve()
            if SITE not in f.parents and f != SITE:  # block path traversal
                return self._send(404, b"not found", "text/plain")
        if not f.is_file():
            return self._send(404, b"not found", "text/plain")
        ctype = mimetypes.guess_type(str(f))[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype == "application/json":
            ctype += "; charset=utf-8"
        self._send(200, f.read_bytes(), ctype)

    do_HEAD = do_GET

    def _screening(self):
        d = load("screening.json", {"jobs": [], "generated": None})
        gen = generated()
        live = {(j["url"], j["title"].lower()) for j in load("jobs.json", {"jobs": []}).get("jobs", [])}
        hidden = {"older": 0, "closed": 0}
        keep = []
        decisions = load("decisions.json", {})
        for j in d.get("jobs", []):
            dec = decisions.get(jid(j["url"], j["title"]))
            if j.get("eligibility") == "blocked" and not dec:
                j["excluded"] = True  # hard blocker: shown only under the Excluded tab
            if dec:  # jobs you marked stay visible in their tab regardless of age or being taken down
                j["decision"] = dec
                j["open_status"] = "unverified" if "alert" in str(j.get("via", "")) else (
                    "listed" if (j["url"], j["title"].lower()) in live else "closed")
                keep.append(j)
                continue
            try:
                age = (time.time() - time.mktime(time.strptime((j.get("posted") or "")[:10], "%Y-%m-%d"))) / 86400
            except ValueError:
                age = None
            if age is not None and age > MAX_AGE_DAYS:
                hidden["older"] += 1
                continue
            # career-site/API jobs: if the latest fetch no longer lists the posting, it has been taken down
            if "alert" not in str(j.get("via", "")) and (j["url"], j["title"].lower()) not in live:
                hidden["closed"] += 1
                continue
            j["open_status"] = "unverified" if "alert" in str(j.get("via", "")) else "listed"
            keep.append(j)
        d["jobs"], d["hidden"], d["max_age_days"] = keep, hidden, MAX_AGE_DAYS
        for j in d.get("jobs", []):
            i = jid(j["url"], j["title"])
            j["id"] = i
            j["src"] = source_of(j)
            j["has_jd"] = bool(jd_for(j)[0])
            j["title_only"] = not j["has_jd"] and "alert" in str(j.get("via", ""))
            j["gen"] = gen.get(i)
            if j["gen"] and SLUG_RE.match(j["gen"]["slug"]):
                j["gen"] = dict(j["gen"], coverage=gen_coverage(j["gen"]["slug"]))
        d["busy"] = bool(_busy["run"])
        d["runs_today"], d["daily_cap"] = runs_today(), DAILY_CAP
        self._json(200, d)

    def _api_jd(self, q):
        job = find_job(q.get("id", "")) if ID_RE.match(q.get("id", "")) else None
        if not job:
            return self._json(404, {"error": "unknown job"})
        text, src = jd_for(job)
        self._json(200, {"text": text, "source": src})

    def _api_file(self, q):
        i, fname = q.get("id", ""), q.get("file", "")
        g = generated().get(i) if ID_RE.match(i) else None
        if not g or fname not in FILES or fname not in g.get("files", []) or not SLUG_RE.match(g["slug"]):
            return self._send(404, b"not found", "text/plain")
        fp = (RESUME_OUT / g["slug"] / fname).resolve()
        if RESUME_OUT not in fp.parents or not fp.is_file():
            return self._send(404, b"not found", "text/plain")
        view = fname.endswith(".pdf")
        self._send(200, fp.read_bytes(), FILES[fname], {
            "Content-Disposition": f'{"inline" if view else "attachment"}; filename="{g["slug"]}-{fname}"'})

    def _api_mark(self, q):
        i, st = q.get("id", ""), q.get("status", "")
        if not (ID_RE.match(i) and find_job(i)) or st not in ("interested", "applied", "archived", "none"):
            return self._json(400, {"error": "bad request"})
        with _lock:
            dec = load("decisions.json", {})
            if st == "none":
                dec.pop(i, None)
            else:
                dec[i] = {"status": st, "at": time.strftime("%Y-%m-%d %H:%M")}
            DATA.mkdir(exist_ok=True)
            tmp = DATA / "decisions.json.tmp"
            tmp.write_text(json.dumps(dec, indent=1))
            tmp.replace(DATA / "decisions.json")
        self._json(200, {"ok": True, "decision": dec.get(i)})

    # ----------------------------------------------------------------- POST
    def do_POST(self):
        path, q = self._route()
        if self.headers.get("X-Jobs") != "1":
            return self._json(403, {"error": "forbidden"})
        origin = self.headers.get("Origin")
        if origin and urlparse(origin).netloc != self.headers.get("Host"):
            return self._json(403, {"error": "forbidden origin"})
        if path == "/api/mark":
            return self._api_mark(q)
        if path not in ("/api/generate", "/api/cover"):
            return self._json(404, {"error": "not found"})
        i = q.get("id", "")
        job = find_job(i) if ID_RE.match(i) else None
        if not job:
            return self._json(404, {"error": "unknown job"})
        kind = "cover" if path == "/api/cover" else ("resume+cover" if q.get("cover") == "1" else "resume")
        slug = None
        text = ""
        if kind == "cover":
            g = generated().get(i)
            if not g:
                return self._json(400, {"error": "Make the resume first."})
            slug = g["slug"]
            if not SLUG_RE.match(slug) or not (RESUME_OUT / slug / "instance.yaml").is_file():
                return self._json(400, {"error": "The resume folder is missing its inputs."})
        else:
            text, _ = jd_for(job)
            if not text:
                return self._json(400, {"error": "No job description yet for this job."})
        with _lock:
            if _busy["run"]:
                return self._json(409, {"error": "Another run is in progress. Wait for it to finish."})
            if runs_today() >= DAILY_CAP:
                return self._json(429, {"error": f"Daily limit of {DAILY_CAP} runs reached."})
            run = hashlib.sha1(f"{i}{time.time()}".encode()).hexdigest()[:12]
            _runs[run] = {"state": "running", "kind": kind, "started": time.time()}
            _busy["run"] = run
            count_run()
        threading.Thread(target=_worker, args=(run, i, kind, text, slug), daemon=True).start()
        self._json(202, {"run": run})


if __name__ == "__main__":
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"serving on 127.0.0.1:{PORT}", flush=True)
    srv.serve_forever()
