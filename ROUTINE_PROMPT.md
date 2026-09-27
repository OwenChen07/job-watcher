# Routine prompt template

Paste this as the prompt of a Claude Code cloud routine (claude.ai/code/routines) with this repo as its source,
or of a local scheduled task. Replace the three `<<...>>` placeholders. The prompt is private to your account;
never commit your filled-in copy.

---

Find today's most relevant new internship postings and append them to my Google Sheet job tracker.

Setup (run once at the start; never print these values):
```
export JOB_WATCHER_WEBHOOK_URL='<<your Apps Script web app URL>>'
export JOB_WATCHER_TOKEN='<<your TOKEN from apps_script.gs>>'
export JOB_WATCHER_PRIORITY_TERM=W27   # or S27
export JOB_WATCHER_PREFER_CANADA=1     # or 0
```

My profile:
<<paste your profile, see profile.example.md>>

The repo is checked out in the working directory. `job_watcher.py` is stdlib-only Python:
- `fetch` pulls the sources and writes `candidates.json`.
- `post <file>` appends rows through the Apps Script webhook and records seen-state in the sheet's hidden `_seen` tab.

The sources are the SimplifyJobs/Summer2027-Internships listings.json (README and Off-Season), negarprh/Canadian-Tech-Internships-2027, and speedyapply/2027-SWE-College-Jobs and 2027-AI-College-Jobs (US and INTERN_INTL).

Steps:
1. Run the exports above, then `python3 job_watcher.py fetch`. It writes `candidates.json` with the top 30 unseen postings, ranked by a heuristic score. Postings already in the sheet or seen before are removed.
   - If `errors` mentions "sheet read" or "Missing webhook_url", stop and report it. Do not post.
   - If a single source errored, continue with the others and mention it in your report.
2. Read `candidates.json`. Pick up to **10** postings, using the heuristic score as a starting point plus your judgement against my profile. Follow the profile's priorities for term, location and role type.
   - Drop roles that are really hardware, QA-only, IT/support, analyst, sales/solutions, graduate-level, or need US citizenship or clearance.
   - Two postings for the same company and role in different cities count as one; prefer the one matching my location preference.
   - Candidates with `long_term: true` are 8+ month terms. Exclude them unless they are exceptional: a top-tier company, clearly high pay, or an unusually strong match for my background. If you include one, put "8-month" in the notes. "4 or 8 months" postings are not long-term.
   - Choosing fewer than 10 is fine if the rest are weak. Choosing 0 is fine on a quiet day.
3. Write `selected.json` as a JSON array. Each object has exactly these keys:
   - `role`: a clean role name, e.g. "Software Engineer", "Machine Learning Engineer" or "Software Developer". Add a short team qualifier only if it's informative, e.g. "Software Engineer (Infrastructure)". Drop "Intern/Co-op", the term and the year.
   - `company`: the short common name, e.g. "BMO" not "Bank of Montreal".
   - `location`: city only for Canada (e.g. "Toronto", "Montreal"), "Remote" for remote roles, and "US" or a short city like "NYC" or "San Jose" for US roles.
   - `term`: "W27" or "S27". A "W27?" candidate should be verified from the title or URL where possible; otherwise use W27 and say so in the notes.
   - `platform`: the direct application URL.
   - `date_applied`: "".
   - `status`: "Apply later".
   - `notes`: start with "From Claude", e.g. "From Claude · Posted 9/26 · Canada · ML fit · via Simplify". Include any caveat from the candidate's `note` field.
4. Run `python3 job_watcher.py post selected.json`. It must print `"ok": true`. This also records all fetched candidates as seen, so run it even when `selected.json` is `[]`.
5. Report briefly:
   - how many were added and the first sheet row number
   - a compact list of company, role, location and term for each posting added
   - notable skips and why
   - any source errors

Constraints:
- Never apply to jobs, submit forms, contact anyone, commit, or push.
- Only append rows; never modify or delete existing sheet rows.
- Never print the token or webhook URL.
