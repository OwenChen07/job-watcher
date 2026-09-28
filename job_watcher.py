#!/usr/bin/env python3
"""Daily internship watcher: scans GitHub internship lists, scores new postings against
your preferences, and appends the best ones to a Google Sheet via an Apps Script webhook.

Subcommands:
  fetch   Pull all sources, filter, score, dedupe against seen-state and the sheet,
          write candidates.json (ranked). Prints a compact table.
  post    Append a JSON list of rows (default: selected.json) to the sheet and mark
          every fetched candidate as seen.
  ping    Check the webhook config works (reads existing sheet rows).

Config (env vars take precedence over config.json):
  JOB_WATCHER_WEBHOOK_URL, JOB_WATCHER_TOKEN
  JOB_WATCHER_PRIORITY_TERM  W27 (default) or S27: the term that gets the bigger boost
  JOB_WATCHER_PREFER_CANADA  1 (default) or 0
Seen-state is kept in the sheet's hidden _seen tab, so runs can be stateless (cloud).

Stdlib only.
"""
import html
import os
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
CANDIDATES_PATH = HERE / "candidates.json"
CONFIG_PATH = HERE / "config.json"

def load_config():
    cfg = {}
    try:
        cfg = json.loads(CONFIG_PATH.read_text())
    except Exception:
        pass
    env = os.environ
    return {
        "webhook_url": env.get("JOB_WATCHER_WEBHOOK_URL") or cfg.get("webhook_url", ""),
        "token": env.get("JOB_WATCHER_TOKEN") or cfg.get("token", ""),
        "priority_term": (env.get("JOB_WATCHER_PRIORITY_TERM") or cfg.get("priority_term") or "W27").upper(),
        "prefer_canada": str(env.get("JOB_WATCHER_PREFER_CANADA", cfg.get("prefer_canada", "1"))).lower() not in ("0", "false", "no"),
    }


# Only consider postings this recent (days). Seen-state is the primary dedupe;
# this just bounds the first run and ignores stale re-shuffles.
MAX_AGE_DAYS = 3
TOP_N_CANDIDATES = 30

SIMPLIFY_JSON = "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/.github/scripts/listings.json"
CANADA_MD = "https://raw.githubusercontent.com/negarprh/Canadian-Tech-Internships-2027/main/README.md"
SPEEDY = [
    ("speedyapply-SWE", "https://raw.githubusercontent.com/speedyapply/2027-SWE-College-Jobs/main/README.md"),
    ("speedyapply-SWE-intl", "https://raw.githubusercontent.com/speedyapply/2027-SWE-College-Jobs/main/INTERN_INTL.md"),
    ("speedyapply-AI", "https://raw.githubusercontent.com/speedyapply/2027-AI-College-Jobs/main/README.md"),
    ("speedyapply-AI-intl", "https://raw.githubusercontent.com/speedyapply/2027-AI-College-Jobs/main/INTERN_INTL.md"),
]

NOW = datetime.now(timezone.utc)

# ---------------------------------------------------------------- helpers

def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "job-watcher"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8", "replace")


def strip_tags(s):
    return html.unescape(re.sub(r"<[^>]+>", " ", s)).replace(" ", " ").strip()


def norm(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def norm_company(s):
    s = norm(s)
    s = re.sub(r"\b(inc|llc|ltd|corp|corporation|co|technologies|technology|group|the)\b", " ", s)
    return re.sub(r"\s+", "", s)


def norm_url(u):
    if not u:
        return ""
    p = urllib.parse.urlsplit(u.strip())
    q = [(k, v) for k, v in urllib.parse.parse_qsl(p.query)
         if not k.lower().startswith("utm") and k.lower() not in ("ref", "embed", "source")]
    return urllib.parse.urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/"),
                                    urllib.parse.urlencode(q), ""))


def load_json(path, default):
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return default

# ---------------------------------------------------------------- classification

CA_PAT = re.compile(
    r"\b(canada|ontario|quebec|québec|british columbia|alberta|toronto|waterloo|kitchener|ottawa|"
    r"montr[eé]al|vancouver|calgary|edmonton|markham|mississauga|burnaby|halifax|winnipeg|"
    r"victoria|kanata|oakville|brampton|vaughan|richmond hill|guelph|hamilton|london, on)\b"
    r"|,\s*(on|qc|bc|ab|ns|mb|sk|nb)\b", re.I)

EXCLUDE_ROLE = re.compile(
    r"\b(hardware|mechanical|electrical|civil|chemical|pcb|pcba|asic|fpga|rtl|analog|rf|"
    r"manufacturing|process engineer|sales|marketing|recruit|finance|accounting|legal|"
    r"product manag|program manag|project manag|business analyst|designer|ux|ui/ux|"
    r"trader|trading intern|supply chain|operations intern|hr |human resources|optical|"
    r"ecu|avionics|aerospace engineer|structural|materials|biomedical|"
    r"phd|ph\.d|master'?s|ms/phd|mba|new grad|graduate program|apprentice)\b", re.I)

# Postings that require the applicant to already be in their graduating year; the title rarely
# says this, but it's a hard "can't apply" when it does, unlike the soft scoring below.
GRAD_YEAR_EXCLUDE = re.compile(
    r"\b(class of|graduating|expected graduation)\D{0,12}20(2[5-8])\b|"
    r"\bsenior[- ]year\b|\bfinal[- ]year\b|\brising senior\b", re.I)

# Titles centered on mobile app dev (iOS/Android/React Native) - weak personal fit, not a hard
# exclude since e.g. "Software Engineer, Mobile Platform" can still be backend-adjacent.
MOBILE_FOCUS = re.compile(r"\b(ios|android|swift|kotlin|react native|mobile app)\b", re.I)

ML_PAT = re.compile(r"\b(machine learning|ml|ai|artificial intelligence|deep learning|llm|"
                    r"computer vision|nlp|applied scien|research engineer|genai|perception)\b", re.I)
SWE_PAT = re.compile(r"\b(software|developer|swe|sde|backend|back end|full ?stack|frontend|"
                     r"front end|platform engineer|infrastructure|devops|sre|site reliability|"
                     r"mobile|cloud engineer|web|programmer|application develop)\b", re.I)
GENERIC_ENG = re.compile(r"\b(engineer|engineering)\b", re.I)
SOFTWARE_CATS = {"Software", "Software Engineering", "AI/ML/Data", "Data Science, AI & Machine Learning"}
# 8+ month commitments ("8 months", "8-month", "12 month", "6-8 months", "fall/winter"); "4 or 8
# months" still allows 4. Many extended co-ops (esp. Canadian) never spell out the length in the
# listing at all - see FLEX_4/duration below, which flags that case for caution instead.
LONG_TERM = re.compile(r"\b(8|12|16)[ -]?months?\b|\b(eight|twelve|sixteen)[ -]?months?\b|\b8[ -]?mo\b|"
                       r"\bextended\b|\boff[- ]cycle\b|\bfall\s*/\s*winter\b|\bwinter\s*/\s*summer\b", re.I)
FLEX_4 = re.compile(r"\b4[\s_-]*(or|/|to|-)[\s_-]*8\b|\b(4|four)[ -]?months?\b", re.I)
LOW_FIT = re.compile(r"\b(analyst|firmware|embedded|solutions engineer|support engineer|it intern)\b", re.I)
DATA_PAT = re.compile(r"\b(data scien|data engineer|analytics engineer)\b", re.I)
QA_PAT = re.compile(r"\b(qa|quality assurance|test|tester|verification|validation)\b", re.I)
# Common ML/infra/backend keywords -> small relevance bonus
FIT_PAT = re.compile(r"\b(pytorch|infrastructure|platform|backend|back end|full ?stack|devops|"
                     r"ci/cd|cloud|distributed|ml ?ops|mlops|inference|llm|agent|developer tools|"
                     r"compiler|kubernetes|data platform)\b", re.I)


def role_kind(title, category=""):
    if EXCLUDE_ROLE.search(title) or GRAD_YEAR_EXCLUDE.search(title):
        return None
    if ML_PAT.search(title):
        return "ML"
    if DATA_PAT.search(title):
        return "DS"
    if SWE_PAT.search(title):
        return "SWE"
    # bare "Engineering Intern" only counts when Simplify filed it under software
    if GENERIC_ENG.search(title) and category in SOFTWARE_CATS:
        return "SWE"
    return None


def title_key(title):
    t = norm(title)
    t = re.sub(r"\b(winter|summer|spring|fall|autumn)\b|\b20\d\d\b|\b(co op|coop|intern|internship|"
               r"\d+ ?months?|4|8|12|16)\b", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def term_from_text(text):
    t = text.lower()
    if re.search(r"winter\b[^|]{0,20}2027|2027[^|]{0,5}winter|jan(uary)?\.?\s*(-|–|to)?\s*\w*\s*2027|spring\s*2027|w27", t):
        return "W27"
    if re.search(r"summer\b[^|]{0,20}2027|may\s*2027|s27", t):
        return "S27"
    if re.search(r"fall\s*2027|sept(ember)?\s*2027", t):
        return "F27"
    if re.search(r"fall\s*2026|summer\s*2026|winter\s*2026|2026", t):
        return "OLD"
    return None


def short_location(locs):
    """Collapse a list of location strings to the style used in the sheet."""
    locs = [l.strip() for l in locs if l and l.strip()]
    if not locs:
        return "?"
    ca = [l for l in locs if CA_PAT.search(l)]
    pick = ca if ca else locs
    cities = []
    for l in pick:
        if re.search(r"remote", l, re.I):
            c = "Remote" + (" (Canada)" if CA_PAT.search(l) else "")
        else:
            c = re.split(r",", l)[0].strip()
            c = {"SF": "San Francisco", "NYC": "NYC", "New York City": "NYC", "New York": "NYC"}.get(c, c)
        if c not in cities:
            cities.append(c)
    s = ", ".join(cities[:3]) + (f" +{len(cities) - 3}" if len(cities) > 3 else "")
    if len(locs) > len(pick):
        s += " (+US)"
    return s

# ---------------------------------------------------------------- sources

def from_simplify():
    out = []
    data = json.loads(get(SIMPLIFY_JSON))
    cutoff = (NOW - timedelta(days=MAX_AGE_DAYS)).timestamp()
    for x in data:
        if not x.get("active") or not x.get("is_visible", True):
            continue
        if (x.get("date_posted") or 0) < cutoff:
            continue
        if x.get("sponsorship") == "U.S. Citizenship is Required":
            continue
        degrees = x.get("degrees") or []
        if degrees and "Bachelor's" not in degrees:
            continue
        terms = x.get("terms") or []
        term, note = None, ""
        if "Winter 2027" in terms:
            term = "W27"
        elif "Spring 2027" in terms:
            term, note = "W27", "US 'Spring 2027' (~Jan-May)"
        elif "Winter 2026" in terms:
            # New postings tagged Winter 2026 are almost always the upcoming winter.
            term, note = "W27", "tagged 'Winter 2026' - verify start date"
        elif "Summer 2027" in terms:
            term = "S27"
        else:
            term = term_from_text(x.get("title", ""))
        if term not in ("W27", "S27"):
            continue
        out.append(dict(
            source="Simplify", company=x.get("company_name", ""), title=x.get("title", ""),
            locations=x.get("locations") or [], term=term, url=x.get("url", ""),
            posted=datetime.fromtimestamp(x["date_posted"], timezone.utc).date().isoformat(),
            category=x.get("category", ""), note=note, top_company=False,
        ))
    return out


def from_canada():
    out = []
    md = get(CANADA_MD)
    last_company = ""
    cutoff = (NOW - timedelta(days=MAX_AGE_DAYS)).date()
    for line in md.splitlines():
        if not line.startswith("|") or "---" in line or line.startswith("| Company"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 5:
            continue
        company, role, loc, apply_cell, date_s = cells[:5]
        company = strip_tags(re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", company)).strip("* ")
        if company in ("↳", ""):
            company = last_company
        else:
            last_company = company
        m = re.search(r"\]\((https?://[^)\s]+)\)\s*$", apply_cell) or re.search(r"\((https?://[^)\s]+)\)", apply_cell)
        if not m or "🔒" in apply_cell:
            continue
        try:
            posted = datetime.strptime(date_s.strip(), "%b %d, %Y").date()
        except ValueError:
            continue
        if posted < cutoff:
            continue
        term = term_from_text(role)
        note = ""
        if term is None:
            term, note = "W27?", "term not stated"
        if term not in ("W27", "S27", "W27?"):
            continue
        out.append(dict(source="Canadian-Tech-Internships", company=company, title=strip_tags(role),
                        locations=[strip_tags(loc)], term=term, url=m.group(1),
                        posted=posted.isoformat(), category="", note=note, top_company=False))
    return out


def from_speedy(name, url):
    out = []
    md = get(url)
    section = ""
    for line in md.splitlines():
        if "TABLE_FAANG_START" in line:
            section = "FAANG"
        elif "TABLE_QUANT_START" in line:
            section = "QUANT"
        elif re.search(r"TABLE_.*_START", line):
            section = "OTHER"
        if not line.startswith("| <") and not line.startswith("| **"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        company = strip_tags(cells[0])
        title = strip_tags(cells[1])
        loc = strip_tags(cells[2])
        link = re.search(r'href="([^"]+)"', cells[-2])
        age = re.match(r"(\d+)d", cells[-1].strip())
        if not link or not age:
            continue
        days = int(age.group(1))
        if days > MAX_AGE_DAYS:
            continue
        term = term_from_text(title)
        note = ""
        if term is None:
            term, note = "S27", "term inferred (2027 list, no season in title)"
        if term not in ("W27", "S27"):
            continue
        out.append(dict(source=name, company=company, title=title, locations=[loc], term=term,
                        url=link.group(1), posted=(NOW - timedelta(days=days)).date().isoformat(),
                        category="", note=note, top_company=(section == "FAANG")))
    return out

# ---------------------------------------------------------------- scoring

def score(c, cfg):
    s = 0
    reasons = []
    if cfg["priority_term"] == "S27":
        s += {"W27": 20, "W27?": 15, "S27": 40}[c["term"]]
    else:
        s += {"W27": 40, "W27?": 25, "S27": 20}[c["term"]]
    loc_text = " ".join(c["locations"])
    c["canada"] = bool(CA_PAT.search(loc_text))
    if c["canada"] and cfg["prefer_canada"]:
        s += 30
        reasons.append("Canada")
    elif re.search(r"remote", loc_text, re.I):
        s += 8
        reasons.append("Remote")
    kind = c["kind"]
    s += {"ML": 22, "SWE": 20, "DS": 8}[kind]
    if FIT_PAT.search(c["title"]):
        s += 5
        reasons.append("resume fit")
    if QA_PAT.search(c["title"]) and kind != "ML":
        s -= 8
    if LOW_FIT.search(c["title"]):
        s -= 10
    if MOBILE_FOCUS.search(c["title"]):
        s -= 12
    title_url = c["title"] + " " + c["url"]
    duration_stated = bool(LONG_TERM.search(title_url) or FLEX_4.search(title_url))
    c["long_term"] = bool(LONG_TERM.search(title_url)) and not FLEX_4.search(title_url)
    if c["long_term"]:
        s -= 25
        c["note"] = "; ".join(x for x in (c["note"], "8+ month term") if x)
    elif not duration_stated:
        # Listing doesn't say how long the term is; several past "4-month" assumptions turned
        # out to be 8-month co-ops once opened, so treat unstated length as a mild risk, not a pass.
        s -= 5
        c["note"] = "; ".join(x for x in (c["note"], "duration not stated - verify not 8-month") if x)
    if c["top_company"] or "🔥" in c.get("raw_company", ""):
        s += 10
        reasons.append("top co.")
    if len(c["sources"]) > 1:
        s += 3
    c["score"] = s
    c["reasons"] = reasons
    return c

# ---------------------------------------------------------------- sheet webhook

def webhook_cfg():
    cfg = load_config()
    if not cfg["webhook_url"] or not cfg["token"]:
        sys.exit("Missing webhook_url/token (set JOB_WATCHER_WEBHOOK_URL / JOB_WATCHER_TOKEN or config.json)")
    return cfg


def sheet_state():
    """-> (existing rows, seen dict) from the sheet."""
    cfg = webhook_cfg()
    url = cfg["webhook_url"] + "?" + urllib.parse.urlencode({"token": cfg["token"]})
    res = json.loads(get(url))
    if not res.get("ok"):
        sys.exit(f"Webhook error: {res.get('error')}")
    return res["rows"], res.get("seen", {})


def post_payload(payload):
    cfg = webhook_cfg()
    body = json.dumps({"token": cfg["token"], **payload}).encode()
    req = urllib.request.Request(cfg["webhook_url"], data=body,
                                 headers={"Content-Type": "application/json"})
    # Apps Script answers POST with a 302 to googleusercontent; urllib follows it as a GET,
    # which is exactly how the result is meant to be fetched.
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def in_sheet(c, existing):
    """True if the sheet already has this posting (same URL, or same company+term with a
    role whose words are all contained in this posting's title)."""
    u = norm_url(c["url"])
    cc = norm_company(c["company"])
    tw = set(norm(c["title"]).split())
    for r in existing:
        if u and norm_url(r.get("url", "")) == u:
            return True
        rterm = str(r.get("term", "")).strip().upper()
        # a blank Term on an existing row matches any term
        if norm_company(r.get("company", "")) == cc and rterm in ("", c["term"].rstrip("?")):
            rw = set(norm(r.get("role", "")).split()) - {"intern", "internship", "co", "op", "coop"}
            if rw and rw <= tw:
                return True
    return False

# ---------------------------------------------------------------- commands

def cmd_fetch():
    cfg = load_config()
    raw, errors = [], []
    for label, fn in [("Simplify", from_simplify), ("Canada", from_canada)] + \
                     [(n, (lambda n=n, u=u: from_speedy(n, u))) for n, u in SPEEDY]:
        try:
            got = fn()
            raw += got
            print(f"  {label}: {len(got)} recent W27/S27 postings", file=sys.stderr)
        except Exception as e:
            errors.append(f"{label}: {e}")
            print(f"  {label}: ERROR {e}", file=sys.stderr)

    # merge duplicates across sources
    merged, by_url = {}, {}
    for c in raw:
        kind = role_kind(c["title"], c.get("category", ""))
        if not kind:
            continue
        c["kind"] = kind
        c["raw_company"] = c["company"]
        c["company"] = c["company"].replace("🔥", "").strip()
        key = norm_company(c["company"]) + "|" + title_key(c["title"])
        ukey = norm_url(c["url"])
        if key not in merged and ukey in by_url:
            key = by_url[ukey]
        if key in merged:
            m = merged[key]
            m["sources"].append(c["source"])
            m["top_company"] |= c["top_company"]
            if c["term"] != m["term"] and "S27" in (c["term"], m["term"]):
                # same role posted separately per term: keep the winter link, mention the other
                if c["term"].startswith("W27"):
                    for k in ("title", "url", "term", "posted", "locations"):
                        m[k] = c[k]
                m["note"] = "; ".join(x for x in (m["note"], "also posted for S27") if x)
            continue
        c["sources"] = [c["source"]]
        c["key"] = key
        merged[key] = c
        by_url[ukey] = key

    try:
        existing, seen = sheet_state()
    except SystemExit as e:
        errors.append(f"sheet read: {e}")
        existing, seen = [], {}

    cands, skipped_seen, skipped_sheet = [], 0, 0
    for c in merged.values():
        if c["key"] in seen or norm_url(c["url"]) in seen:
            skipped_seen += 1
            continue
        if in_sheet(c, existing):
            skipped_sheet += 1
            continue
        cands.append(score(c, cfg))

    cands.sort(key=lambda c: (c["score"], c["posted"]), reverse=True)
    out = []
    for i, c in enumerate(cands):
        out.append(dict(
            rank=i + 1, score=c["score"], company=c["company"], title=c["title"],
            location=short_location(c["locations"]), locations_full=c["locations"],
            term=c["term"], kind=c["kind"], canada=c["canada"], long_term=c["long_term"], url=c["url"],
            posted=c["posted"], sources=c["sources"], reasons=c["reasons"], note=c["note"],
            key=c["key"],
        ))
    CANDIDATES_PATH.write_text(json.dumps(
        {"generated": NOW.isoformat(), "errors": errors, "total_new": len(out),
         "skipped_seen": skipped_seen, "skipped_in_sheet": skipped_sheet,
         "all_keys": [c["key"] for c in out],
         "candidates": out[:TOP_N_CANDIDATES]}, indent=1, ensure_ascii=False))

    print(f"new={len(out)} skipped_seen={skipped_seen} skipped_in_sheet={skipped_sheet} errors={errors}")
    for c in out[:TOP_N_CANDIDATES]:
        print(f"{c['rank']:>2} {c['score']:>3} {c['term']:<4} {c['kind']:<3} {c['company'][:22]:<22} "
              f"{c['title'][:60]:<60} {c['location'][:28]}")


def cmd_post(path):
    rows = load_json(path, None)
    if rows is None:
        sys.exit(f"Can't read {path}")
    cols = ["role", "company", "location", "term", "platform", "date_applied", "status", "notes"]
    for r in rows:
        for k in cols:
            r.setdefault(k, "")
        n = str(r["notes"]).strip()
        if not n.startswith("From Claude"):
            r["notes"] = "From Claude" + (" · " + n if n else "")
    # mark everything from today's fetch as seen, so overflow isn't re-considered tomorrow
    cand = load_json(CANDIDATES_PATH, {})
    keys = list(cand.get("all_keys", [])) + [norm_url(r["platform"]) for r in rows]
    res = post_payload({"rows": rows, "seen": keys, "today": NOW.date().isoformat()})
    print(json.dumps(res))
    if not res.get("ok"):
        sys.exit(1)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "fetch"
    if cmd == "fetch":
        cmd_fetch()
    elif cmd == "post":
        cmd_post(sys.argv[2] if len(sys.argv) > 2 else str(HERE / "selected.json"))
    elif cmd == "ping":
        rows, seen = sheet_state()
        print(f"OK: {len(rows)} existing rows, {len(seen)} seen keys")
    else:
        sys.exit(__doc__)
