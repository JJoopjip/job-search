#!/usr/bin/env python3
"""Write fictional demo data to sample_data/ so the page runs on a fresh clone (no Gmail, no private data).
Every company, posting and grade below is invented. Run: python3 make_sample.py
Then: JOBS_DATA_DIR=sample_data JOBS_MAX_AGE_DAYS=36500 python3 server.py 5100
"""
import datetime
import json
from pathlib import Path

OUT = Path(__file__).parent / "sample_data"
D = lambda n: (datetime.date.today() - datetime.timedelta(days=n)).isoformat()


def job(company, title, loc, market, grade, verdict, via, posted, one, skills, level, domain, jd="", prof="pm", elig="ok", n=1):
    kind = {"linkedin": "https://www.linkedin.com/jobs/view/", "indeed": "https://ca.indeed.com/viewjob?jk="}.get(via)
    url = (kind + f"demo{n:04d}") if kind else f"https://jobs.example.com/{company.lower().replace(' ', '-')}/{n}"
    return {"url": url, "company": company, "title": title, "location": loc, "market": market, "posted": posted,
            "profile": prof, "grade": grade, "verdict": verdict, "one_line": one,
            "jd_summary": jd or "Alert snippet only: title, company and location.",
            "requirements_seen": [], "coverage": [], "gaps": [], "eligibility": elig, "eligibility_note": "",
            "tailor_with": prof, "via": ("alert-" + via) if kind else via,
            "why": {"level": level, "skills": skills, "domain": domain}}


JOBS = [
    job("Northwind Biologics", "Partnerships Associate", "Toronto, ON", "toronto", "A-", "apply", "linkedin", D(2),
        "Coordinator-level partnerships role at a biotech: strong skills and domain fit.",
        "[assumed] partner onboarding and relationship tracking match the sample profile.", "Associate level: no penalty.",
        "Biotech: health bonus.", prof="bd", n=1),
    job("Maple Clinical Research", "Clinical Project Coordinator", "Remote, Canada", "remote", "B+", "apply", "greenhouse", D(3),
        "Clinical research coordination, mid-level, remote within Canada.",
        "Real JD: timelines, vendor coordination and reporting fit.", "Coordinator level: no penalty.",
        "CRO: health bonus.", jd="Coordinate study start-up, track timelines and budgets, liaise with sites and vendors.", n=2),
    job("Lakeshore Health Tech", "Business Development Specialist", "Toronto, ON", "toronto", "B", "apply", "linkedin", D(5),
        "Health-tech BD specialist; good fit, title only.",
        "[assumed] pipeline building and partner outreach.", "Specialist: no penalty.", "Health-tech: bonus.", prof="bd", n=3),
    job("Granite Logistics", "Program Manager", "Toronto, ON", "toronto", "C+", "stretch", "indeed", D(4),
        "Program manager in logistics: skills transfer, non-health, scope unclear.",
        "[assumed] program governance and stakeholder reporting.", "Possible 5+ years ask: lose one notch.",
        "Non-health: no bonus.", n=4),
    job("Orchid Pharmacy Group", "Regional Operations Manager", "Mississauga, ON", "toronto", "C", "stretch", "linkedin", D(6),
        "Manager title is a level stretch; domain is a good match.",
        "[assumed] multi-site operations.", "Manager+: lose two notches.", "Pharmacy: health bonus.", n=5),
    job("Sunrise Staffing", "Administrative Assistant", "Toronto, ON", "toronto", "D", "skip", "indeed", D(1),
        "Clerical role well below the profile.", "[assumed] scheduling and filing.", "Far below level: lose a notch.",
        "Non-health.", n=6),
    job("Atlas Defence Systems", "Project Coordinator", "Remote, US", "remote", "B", "skip", "linkedin", D(2),
        "Good skills fit but the role requires US security clearance.",
        "[assumed] coordination skills fit.", "Coordinator: no penalty.", "Defence: no bonus.",
        elig="blocked", n=7),
    job("Blue Heron Medical", "Partnership Development Coordinator", "Bangkok, Thailand", "bangkok", "B-", "apply",
        "lever", D(8), "Medtech partnership coordinator in Bangkok; English working language.",
        "Real JD: distributor liaison and tender tracking.", "Coordinator: no penalty.", "Medtech: bonus.",
        jd="Support distributor partnerships across ASEAN, track tenders and prepare quarterly reviews.", prof="bd", n=8),
]
OUT.mkdir(exist_ok=True)
(OUT / "screening.json").write_text(json.dumps({"generated": datetime.datetime.now().isoformat(timespec="seconds"),
                                                "candidate_profiles": ["bd", "pm"], "jobs": JOBS}, indent=1))
live = [{"title": j["title"], "location": j["location"], "url": j["url"], "desc": "Demo description. " * 30}
        for j in JOBS if not j["via"].startswith("alert")]
(OUT / "jobs.json").write_text(json.dumps({"fetched": datetime.date.today().isoformat(), "jobs": live}, indent=1))
print("wrote", OUT, len(JOBS), "jobs")
