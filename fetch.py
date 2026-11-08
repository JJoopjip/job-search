#!/usr/bin/env python3
"""Fetch open jobs from public ATS APIs (Greenhouse, Lever, Ashby), filter, write data/jobs.json.

Read-only. Public endpoints, no login. Never applies to anything.
Usage: python3 fetch.py            (all boards)
       python3 fetch.py --board veeva
"""
import argparse
import html
import json
import re
import sys
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent
_env = ROOT / ".env"  # optional private keys: ADZUNA_APP_ID, ADZUNA_APP_KEY, JOOBLE_KEY (git-ignored)
if _env.is_file():
    import os
    for _line in _env.read_text().splitlines():
        if "=" in _line and not _line.lstrip().startswith("#"):
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))
CFG =json.loads((ROOT / "sources.json").read_text())
UA = {"User-Agent": "personal-job-screener/0.1 (read-only; contact: owner)"}


def get_json(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def strip_html(s):
    s = html.unescape(s or "")
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def fetch_greenhouse(b):
    d = get_json(f"https://boards-api.greenhouse.io/v1/boards/{b['slug']}/jobs?content=true")
    for j in d.get("jobs", []):
        yield dict(title=j["title"], location=(j.get("location") or {}).get("name", ""),
                   url=j["absolute_url"], desc=strip_html(j.get("content")),
                   posted=j.get("updated_at", ""), workplace="")


def fetch_lever(b):
    d = get_json(f"https://api.lever.co/v0/postings/{b['slug']}?mode=json")
    for j in d:
        cat = j.get("categories") or {}
        loc = cat.get("location", "")
        if cat.get("allLocations"):
            loc = "; ".join(cat["allLocations"])
        posted = ""
        if j.get("createdAt"):
            posted = datetime.fromtimestamp(j["createdAt"] / 1000, timezone.utc).strftime("%Y-%m-%d")
        yield dict(title=j["text"], location=loc, url=j["hostedUrl"],
                   desc=j.get("descriptionPlain") or strip_html(j.get("description")),
                   posted=posted, workplace=j.get("workplaceType", ""))


def fetch_ashby(b):
    d = get_json(f"https://api.ashbyhq.com/posting-api/job-board/{b['slug']}")
    for j in d.get("jobs", []):
        if j.get("isListed") is False:
            continue
        loc = j.get("location", "")
        secondary = [s.get("location", "") for s in (j.get("secondaryLocations") or [])]
        if secondary:
            loc = "; ".join([loc] + secondary)
        wp = "remote" if j.get("isRemote") else (j.get("workplaceType") or "")
        yield dict(title=j["title"], location=loc, url=j.get("jobUrl", ""),
                   desc=j.get("descriptionPlain") or strip_html(j.get("descriptionHtml")),
                   posted=(j.get("publishedAt") or "")[:10], workplace=wp)


FETCHERS = {"greenhouse": fetch_greenhouse, "lever": fetch_lever, "ashby": fetch_ashby}


def any_term(text, terms):
    t = text.lower()
    return any(x in t for x in terms)


def classify(job):
    """Return (market, eligibility) or None to drop."""
    loc = job["location"].lower()
    blob = (job["location"] + " " + job["workplace"]).lower()
    is_remote = "remote" in blob
    if any_term(loc, CFG["toronto_terms"]):
        return "toronto", "ok"
    if is_remote:
        if any_term(loc, CFG["canada_terms"]):
            return "remote", "canada-ok"
        generic = re.sub(r"[^a-z ]", " ", loc).split()
        generic = [w for w in generic if w not in ("remote", "worldwide", "anywhere", "global")]
        if not generic and not any_term(loc, CFG["us_only_terms"]) and "us" not in loc.split():
            return "remote", "check-eligibility"  # location is just "Remote"/blank
        # Remote but tied to a specific place (or US-only): keep only if the text says Canada
        if "canada" in job["desc"].lower():
            return "remote", "canada-mentioned-in-text"
        return None
    return None


def title_ok(title):
    t = title.lower()
    if any(x in t for x in CFG["title_exclude"]):
        return False
    return any(x in t for x in CFG["title_include"])


def run_board(b):
    out, total, err = [], 0, None
    try:
        for j in FETCHERS[b["ats"]](b):
            total += 1
            if not title_ok(j["title"]):
                continue
            c = classify(j)
            if not c:
                continue
            j.update(company=b["company"], industry=b["industry"], market=c[0], eligibility=c[1],
                     source=f"{b['ats']}:{b['slug']}")
            out.append(j)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, KeyError, json.JSONDecodeError) as e:
        err = f"{type(e).__name__}: {e}"
    return b, total, out, err


def post_json(url, payload):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={**UA, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def agg_himalayas(cfg):
    """Himalayas public API (no key). Attribution: jobs link back to himalayas.app."""
    import time
    from urllib.parse import quote
    for q in cfg["queries"]:
        for page in range(1, cfg["pages_per_query"] + 1):
            d = get_json(f"https://himalayas.app/jobs/api/search?q={quote(q)}&country={quote(cfg['country'])}&page={page}")
            for j in d.get("jobs", []):
                restr = j.get("locationRestrictions") or []
                loc = ", ".join(restr) if len(restr) <= 3 else f"{cfg['country']} (+{len(restr)-1} countries)"
                pub = int(j["pubDate"]) if str(j.get("pubDate", "")).isdigit() else 0
                yield dict(title=j["title"], location=loc, workplace="remote",
                           url=j.get("applicationLink") or j.get("guid", ""),
                           desc=strip_html(j.get("description")),
                           posted=datetime.fromtimestamp(pub, timezone.utc).strftime("%Y-%m-%d") if pub else "",
                           company=j["companyName"], via="himalayas", seniority=",".join(j.get("seniority") or []),
                           forced=("remote", "canada-ok"))
            time.sleep(0.5)


def agg_adzuna(cfg):
    """Adzuna API. Needs free keys in env ADZUNA_APP_ID / ADZUNA_APP_KEY. Descriptions are truncated snippets."""
    import os, time
    from urllib.parse import quote
    app_id, app_key = os.environ.get("ADZUNA_APP_ID"), os.environ.get("ADZUNA_APP_KEY")
    if not (app_id and app_key):
        raise KeyError("ADZUNA_APP_ID / ADZUNA_APP_KEY not set (skipped)")
    for where in cfg["where"]:
        for q in cfg["queries"]:
            for page in range(1, cfg["pages_per_query"] + 1):
                d = get_json(f"https://api.adzuna.com/v1/api/jobs/{cfg['country']}/search/{page}?app_id={app_id}&app_key={app_key}"
                             f"&results_per_page=50&what={quote(q)}&where={quote(where)}&max_days_old={cfg['max_days_old']}")
                for j in d.get("results", []):
                    yield dict(title=strip_html(j["title"]), location=(j.get("location") or {}).get("display_name", ""),
                               url=j["redirect_url"], desc=strip_html(j.get("description")), workplace="",
                               posted=(j.get("created") or "")[:10], company=(j.get("company") or {}).get("display_name", ""),
                               via="adzuna", desc_truncated=True)
                time.sleep(0.5)


def agg_jooble(cfg):
    """Jooble API. Needs a key (by request) in env JOOBLE_KEY. Descriptions are snippets."""
    import os, time
    key = os.environ.get("JOOBLE_KEY")
    if not key:
        raise KeyError("JOOBLE_KEY not set (skipped)")
    for where in cfg["where"]:
        for q in cfg["queries"]:
            d = post_json(f"https://jooble.org/api/{key}", {"keywords": q, "location": where})
            for j in d.get("jobs", []):
                yield dict(title=strip_html(j.get("title")), location=j.get("location", ""), url=j.get("link", ""),
                           desc=strip_html(j.get("snippet")), workplace="", posted=(j.get("updated") or "")[:10],
                           company=j.get("company", ""), via="jooble", desc_truncated=True)
            time.sleep(0.5)


AGGREGATORS = {"himalayas": agg_himalayas, "adzuna": agg_adzuna, "jooble": agg_jooble}


def run_aggregator(name):
    cfg, out, total, err = CFG["aggregators"][name], [], 0, None
    if not cfg.get("enabled"):
        return name, 0, [], "disabled"
    try:
        for j in AGGREGATORS[name](cfg):
            total += 1
            if not title_ok(j["title"]) or not j.get("url"):
                continue
            c = j.pop("forced", None) or classify(j)
            if not c:
                continue
            j.update(industry="health" if HEALTH_RE.search(j["company"] + " " + j["title"]) else "other",
                     market=c[0], eligibility=c[1], source=f"agg:{name}")
            out.append(j)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, KeyError, json.JSONDecodeError) as e:
        err = f"{type(e).__name__}: {e}"
    return name, total, out, err


HEALTH_RE = re.compile(r"\b(pharma\w*|health\w*|medical|medicine|clinical|biotech\w*|bio\w*|therapeutics|diagnostics?|"
                       r"life sciences?|cro|ergomed|medtech|oncolog\w*|patient)\b", re.I)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--board", help="only this slug")
    ap.add_argument("--no-agg", action="store_true", help="skip aggregators")
    args = ap.parse_args()
    boards = [b for b in CFG["boards"] if not args.board or b["slug"] == args.board]
    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(run_board, boards))
    if not args.board and not args.no_agg:
        for name in CFG.get("aggregators", {}):
            n, total, out, err = run_aggregator(name)
            results.append(({"company": f"[agg] {n}", "ats": "api"}, total, out, err))
    jobs, seen = [], set()
    print(f"{'company':22} {'ats':10} {'total':>5} {'kept':>5}  status")
    for b, total, out, err in results:
        print(f"{b['company']:22} {b['ats']:10} {total:5d} {len(out):5d}  {err or 'ok'}")
        for j in out:
            key = (j["url"], j["title"].lower(), j["location"].lower())
            if key in seen:
                continue
            seen.add(key)
            jobs.append(j)
    order = {"toronto": 0, "remote": 1, "bangkok": 2}
    jobs.sort(key=lambda j: (order.get(j["market"], 9), j["industry"] != "health", j["company"], j["title"]))
    out_path = ROOT / "data" / "jobs.json"
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(json.dumps({"fetched": datetime.now(timezone.utc).isoformat(), "jobs": jobs}, indent=1))
    print(f"\n{len(jobs)} matching jobs -> {out_path}")


if __name__ == "__main__":
    sys.exit(main())
