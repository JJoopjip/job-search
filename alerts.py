#!/usr/bin/env python3
"""Read job-alert emails from ONE Gmail label and turn them into job cards for screening.

SAFETY MODEL
- Read-only scope (gmail.readonly). Never sends, deletes, labels or modifies mail.
- Only messages carrying the label "Job Alerts" are listed or fetched. Each message is re-checked for that
  label id before parsing. If the label does not exist, nothing is read.
- Only job cards (title, company, location, link, age) are kept. Email bodies are not stored.
- No website is fetched: alert links are tracking URLs and are kept as-is. Opening them is up to you.

Run with the Python that has Google libs (the Fieldnotes venv):
  /home/joopjip/application_tracker/.venv/bin/python alerts.py --selftest      # parser test on fake emails, no Gmail
  /home/joopjip/application_tracker/.venv/bin/python alerts.py --labels        # list label NAMES only (no mail read)
  /home/joopjip/application_tracker/.venv/bin/python alerts.py --run [--days 14]
Output: data/alert_jobs.json (same fields as data/jobs.json), state in data/alerts_state.json.
"""
import argparse
import base64
import json
import os
import re
import time
import sys
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).parent
LABEL_NAME = os.environ.get("ALERTS_LABEL", "Job Alerts")
TOKEN_PATH = Path(os.environ.get("ALERTS_TOKEN_PATH", "/home/joopjip/application_tracker/token.json"))
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

JOB_HOSTS = ("linkedin.com", "indeed.com", "jobsdb.com", "jobthai.com", "jobbank.gc.ca", "glassdoor.",
             "gojobs.gov.on.ca", "biospace.com", "lifesciencesontario.ca", "petridish.ca", "jobtopgun.com",
             "workventure.com", "monster.", "michaelpage.", "jac-recruitment.")
JOB_PATH_HINTS = ("/jobs/view", "/comm/jobs/view", "viewjob", "/rc/clk", "/pagead/clk", "/job/", "/jobposting", "jk=",
                  "/jobs/", "/en/job", "jobdetail", "job-detail", "/position")
SKIP_TEXT = re.compile(r"^(view|see|apply|unsubscribe|manage|settings|edit|more|help|privacy|terms|share|save|"
                       r"easy apply|view all|see all|view job|view jobs|see jobs|get the app|open|click)\b", re.I)
AGE_RE = re.compile(r"\b(\d+\s*(?:hour|hr|day|week|month)s?\s*ago|today|just posted|just now|new)\b", re.I)
LOC_RE = re.compile(r"(toronto|ontario|\bON\b|canada|remote|bangkok|thailand|กรุงเทพ|mississauga|markham|vaughan|"
                    r"brampton|oakville|hybrid|on-site)", re.I)
LOC_STRONG = re.compile(r"(\((?:remote|hybrid|on-site)\)|,\s*(?:ON|Canada|Ontario)\b|Greater .* Area|^(?:toronto|bangkok|remote)$)", re.I)
NOISE_LINE = re.compile(r"(free trial|cancel any|premium|land your next|new jobs|applicants?|easy apply|promoted|actively recruiting|be an early applicant|response|"
                        r"viewed|connections?|alumni|salary|benefits|\$|฿|thb|per (?:hour|year))", re.I)


class CardParser(HTMLParser):
    """Linear token stream: ('a', href, text) and ('t', text) for text outside anchors."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tokens, self._href, self._buf, self._skip = [], None, [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head"):
            self._skip += 1
        elif tag == "a":
            self._flush_text()
            self._href = dict(attrs).get("href") or ""
            self._buf = []
        elif tag in ("br", "p", "div", "tr", "td", "li"):
            self._flush_text()

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head"):
            self._skip = max(0, self._skip - 1)
        elif tag == "a" and self._href is not None:
            text = " ".join("".join(self._buf).split())
            self.tokens.append(("a", self._href, text))
            self._href, self._buf = None, []
        elif tag in ("p", "div", "tr", "td", "li"):
            self._flush_text()

    def handle_data(self, data):
        if self._skip:
            return
        self._buf.append(data)

    def _flush_text(self):
        if self._href is not None:
            return  # still inside an anchor: keep accumulating
        text = " ".join("".join(self._buf).split())
        self._buf = []
        if text:
            self.tokens.append(("t", text))


def is_job_link(href, text):
    h = href.lower()
    if not h.startswith("http") or len(text) < 6 or SKIP_TEXT.match(text) or len(text) > 140:
        return False
    if "/jobs/search" in h or "/jobs/collections" in h:  # "see more jobs" links, not a single posting
        return False
    return any(x in h for x in JOB_HOSTS) and any(x in h for x in JOB_PATH_HINTS)


def parse_cards(html, source=""):
    p = CardParser()
    p.feed(html)
    p.close()
    toks = p.tokens
    idx = [i for i, t in enumerate(toks) if t[0] == "a" and is_job_link(t[1], t[2])]
    cards = []
    for n, i in enumerate(idx):
        end = idx[n + 1] if n + 1 < len(idx) else min(len(toks), i + 8)
        ctx = [t[1] for t in toks[i + 1:end] if t[0] == "t" and len(t[1]) < 120][:5]
        # only if nothing follows the link (company above title), look back one token
        if not ctx and i > 0 and toks[i - 1][0] == "t" and len(toks[i - 1][1]) < 80 and (n == 0 or i - 1 > idx[n - 1]):
            ctx = [toks[i - 1][1]]
        # split "Company · City" and "Company - Remote" lines into parts
        parts = []
        for c in ctx:
            if " · " in c:
                parts += [x.strip() for x in c.split(" · ")]
            else:
                m = re.match(r"^(.*?)\s[-–]\s((?:remote|hybrid|toronto|bangkok|ontario|canada)\b.*)$", c, re.I)
                parts += [m.group(1).strip(), m.group(2).strip()] if m else [c]
        age = next((m.group(1) for c in parts for m in [AGE_RE.search(c)] if m), "")
        clean = [c for c in parts if not NOISE_LINE.search(c) and not AGE_RE.fullmatch(c.strip())]
        loc = next((c for c in clean if LOC_STRONG.search(c)), "") or next((c for c in clean if LOC_RE.search(c)), "")
        comp = next((c for c in clean if c != loc), "")
        title = toks[i][2]
        if " · " in title:  # some layouts put "Title Company · City" in the link text; company can't be split out
            title, tail = [x.strip() for x in title.split(" · ", 1)]
            loc = loc or tail
        cards.append(dict(title=title, url=toks[i][1], company=comp.strip(" ·-"), location=loc.strip(" ·-"),
                          age=age, context=" | ".join(clean)[:300], source=source))
    return cards


def market_of(card):
    t = (card["location"] + " " + card["context"]).lower()
    if "ottawa" in t or "edmonton" in t or "vancouver" in t:
        return "unknown"
    if any(x in t for x in ("toronto", "mississauga", "markham", "vaughan", "brampton", "oakville", "ontario", ", on")):
        return "toronto"
    if any(x in t for x in ("bangkok", "thailand", "กรุงเทพ")):
        return "bangkok"
    if "remote" in t:
        return "remote"
    return "unknown"


def to_job(card, posted):
    from fetch import HEALTH_RE
    mk = market_of(card)
    return dict(title=card["title"], company=card["company"] or "(see email)", location=card["location"],
                url=card["url"], desc=card["context"], desc_truncated=True, workplace="", posted=posted,
                industry="health" if HEALTH_RE.search(card["company"] + " " + card["title"]) else "other",
                market=mk, eligibility="check-eligibility", seniority="", via="alert:" + (card["source"] or "email"),
                source="alert", age=card["age"], title_match=card.get("title_match", True))


# ---------------------------------------------------------------- Gmail (label-restricted) ----------------
def gmail_service():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        TOKEN_PATH.write_text(creds.to_json())
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def label_id(svc):
    for lb in svc.users().labels().list(userId="me").execute().get("labels", []):
        if lb["name"] == LABEL_NAME:
            return lb["id"]
    return None


def html_of(payload):
    """Return the best HTML (or text wrapped as HTML) body from a Gmail message payload."""
    best = {"text/html": "", "text/plain": ""}

    def walk(part):
        mt = part.get("mimeType", "")
        data = (part.get("body") or {}).get("data")
        if data and mt in best and not best[mt]:
            best[mt] = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")
        for sub in part.get("parts", []) or []:
            walk(sub)

    walk(payload)
    return best["text/html"] or ("<div>" + best["text/plain"].replace("\n", "<br>") + "</div>")


def sender_of(headers):
    frm = next((h["value"] for h in headers if h["name"].lower() == "from"), "")
    m = re.search(r"@([\w.-]+)", frm)
    return m.group(1).lower() if m else ""


def run(days):
    svc = gmail_service()
    lid = label_id(svc)
    if not lid:
        print(f'Label "{LABEL_NAME}" not found. Nothing was read. Create the Gmail label and filter first.')
        return 1
    state_path = ROOT / "data" / "alerts_state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {"done": []}
    done = set(state["done"])
    ids, tok = [], None
    while True:
        r = svc.users().messages().list(userId="me", labelIds=[lid], q=f"newer_than:{days}d", maxResults=100,
                                        pageToken=tok).execute()
        ids += [m["id"] for m in r.get("messages", [])]
        tok = r.get("nextPageToken")
        if not tok or len(ids) >= 300:
            break
    new = [i for i in ids if i not in done]
    jobs_path = ROOT / "data" / "alert_jobs.json"
    jobs = json.loads(jobs_path.read_text())["jobs"] if jobs_path.exists() else []
    seen = set()
    for j in jobs:
        m = re.search(r"/jobs/view/(\d+)", j["url"])
        seen.add((m.group(1),) if m else (j["title"].lower(), j["company"].lower(), j["location"].lower()))
    from fetch import title_ok
    added = dropped = 0
    for mid in new:
        msg = None
        for wait in (0, 15, 30, 60, 60):  # Gmail per-minute quota: back off and retry
            time.sleep(wait)
            try:
                msg = svc.users().messages().get(userId="me", id=mid, format="full").execute()
                break
            except Exception as e:  # noqa: BLE001
                if "rateLimitExceeded" not in str(e) and "Quota exceeded" not in str(e):
                    raise
        if msg is None:
            print("quota still exceeded; stopping early. Progress is saved; run again in a few minutes.")
            break
        if lid not in msg.get("labelIds", []):  # defense in depth
            continue
        posted = __import__("datetime").datetime.fromtimestamp(int(msg["internalDate"]) / 1000).strftime("%Y-%m-%d")
        for c in parse_cards(html_of(msg["payload"]), sender_of(msg["payload"].get("headers", []))):
            # every alert card is kept (user choice); title_ok only marks cards that look off-target
            c["title_match"] = bool(title_ok(c["title"]))
            dropped += 0 if c["title_match"] else 1
            m = re.search(r"/jobs/view/(\d+)", c["url"])  # tracking params differ per email; use the posting id
            k = (m.group(1),) if m else (c["title"].lower(), c["company"].lower(), c["location"].lower())
            if k in seen:
                continue
            seen.add(k)
            jobs.append(to_job(c, posted))
            added += 1
        done.add(mid)
    (ROOT / "data").mkdir(exist_ok=True)
    jobs_path.write_text(json.dumps({"jobs": jobs}, indent=1))
    state_path.write_text(json.dumps({"done": sorted(done)}))
    print(f"label messages in window: {len(ids)}, new: {len(new)}, job cards added: {added}, "
          f"off-target titles kept anyway: {dropped}, total alert jobs: {len(jobs)}")
    return 0


# ---------------------------------------------------------------- self test ----------------------------------
SAMPLES = {
    "linkedin.com": """<html><body><table><tr><td>
<a href="https://www.linkedin.com/comm/jobs/view/111?trk=x">Business Development Associate</a>
<p>Acme Pharma</p><p>Toronto, ON</p><p>3 days ago</p><p>12 applicants</p>
<a href="https://www.linkedin.com/comm/jobs/view/222?trk=x">Senior Staff Engineer</a><p>Foo Inc</p><p>Remote</p>
<a href="https://www.linkedin.com/comm/jobs/view/333?trk=x">Clinical Project Coordinator</a>
<p>MedCo Research · Mississauga, Ontario</p><p>Actively recruiting</p>
<a href="https://www.linkedin.com/comm/jobs/search?x=1">See all jobs</a>
<a href="https://www.linkedin.com/e/unsubscribe">Unsubscribe</a></td></tr></table></body></html>""",
    "indeed.com": """<div><a href="https://ca.indeed.com/rc/clk?jk=abc123&from=ja">Project Manager, Pharmaceutical</a><br>
Zenith Biotech<br>Toronto, ON<br>Just posted<br>
<a href="https://ca.indeed.com/viewjob?jk=def456">Partnerships Manager</a><br>HealthApp - Remote<br></div>""",
    "jobsdb.com": """<div><a href="https://th.jobsdb.com/job/90001">Business Development Manager (Medical Devices)</a>
<div>Siam Medical Co., Ltd.</div><div>Bangkok</div><div>2 days ago</div></div>""",
}


def selftest():
    from fetch import title_ok
    ok = True
    for src, html in SAMPLES.items():
        cards = parse_cards(html, src)
        print(f"\n[{src}] {len(cards)} cards")
        for c in cards:
            keep = title_ok(c["title"])
            print(f'  {"KEEP" if keep else "drop"} | {c["title"][:45]:45} | co={c["company"][:20]!r:24} loc={c["location"][:22]!r:26} '
                  f'age={c["age"]!r:12} market={market_of(c)}')
    li = parse_cards(SAMPLES["linkedin.com"], "linkedin.com")
    checks = [(len(li) == 3, "linkedin: 3 job cards (generic links skipped)"),
              (li[0]["company"] == "Acme Pharma" and "Toronto" in li[0]["location"], "linkedin card 1 company/location"),
              (li[2]["company"] == "MedCo Research" and "Mississauga" in li[2]["location"], "linkedin 'Company · City' split"),
              (len(parse_cards(SAMPLES["indeed.com"], "indeed.com")) == 2, "indeed: 2 cards"),
              (parse_cards(SAMPLES["jobsdb.com"], "jobsdb.com")[0]["location"] == "Bangkok", "jobsdb location"),
              (market_of(parse_cards(SAMPLES["jobsdb.com"])[0]) == "bangkok", "bangkok market")]
    print()
    for passed, name in checks:
        print(("PASS " if passed else "FAIL ") + name)
        ok &= passed
    return 0 if ok else 1


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--labels", action="store_true", help="list label names only; reads no mail")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--days", type=int, default=14)
    a = ap.parse_args()
    if a.selftest:
        sys.exit(selftest())
    if a.labels:
        print("\n".join(sorted(l["name"] for l in gmail_service().users().labels().list(userId="me").execute()["labels"])))
        sys.exit(0)
    if a.run:
        sys.exit(run(a.days))
    ap.print_help()
