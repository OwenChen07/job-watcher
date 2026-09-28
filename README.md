# job-watcher

A daily Claude routine that scans the big 2027 internship lists on GitHub. It picks the postings that best match your profile and appends them to your Google Sheet job tracker, with Status "Apply later" and "From Claude" in the Notes.

Sources:
- [SimplifyJobs/Summer2027-Internships](https://github.com/SimplifyJobs/Summer2027-Internships), both the main and Off-Season lists
- [negarprh/Canadian-Tech-Internships-2027](https://github.com/negarprh/Canadian-Tech-Internships-2027)
- [speedyapply/2027-SWE-College-Jobs](https://github.com/speedyapply/2027-SWE-College-Jobs)
- [speedyapply/2027-AI-College-Jobs](https://github.com/speedyapply/2027-AI-College-Jobs), US and international lists

## How it works
1. `job_watcher.py fetch` pulls every source and keeps SWE/ML/data roles for Winter or Summer 2027 posted in the last 3 days, or the last 7 days if nothing new is left. It drops MS/PhD-only, new-grad and US-citizenship-only postings, merges duplicates across lists, and scores the rest. Scoring favours the priority term, Canada (optional) and ML/infra/backend fit, and penalizes 8+ month terms and QA/analyst roles. Anything already in your sheet or shown before is skipped.
2. Claude reads the top 30 alongside your profile and picks up to 10.
3. `job_watcher.py post` appends them to your sheet through a small Apps Script webhook. It also records every fetched posting in a hidden `_seen` tab, so nothing is shown twice. Because of this the script keeps no local state and runs fine in the cloud.

## Setup
1. **Sheet.** Your tracker needs a tab named `Main` with columns
   `Role | Company | Location | Term | Application platform | Date applied | Status | Notes`.
   If Status has a dropdown, add "Apply later" to it.
2. **Webhook.**
   - In the sheet, open **Extensions → Apps Script** and paste in [`apps_script.gs`](apps_script.gs).
   - Set `TOKEN` to a long random string, e.g. `python3 -c "import secrets;print(secrets.token_urlsafe(24))"`.
   - Go to **Deploy → New deployment → Web app**, with *Execute as: Me* and *Who has access: Anyone*. Copy the `/exec` URL.
   - Only requests carrying the token can read or write.
3. **Test locally (optional).** Run:
   ```
   JOB_WATCHER_WEBHOOK_URL=... JOB_WATCHER_TOKEN=... python3 job_watcher.py ping
   ```
4. **Routine.** At [claude.ai/code/routines](https://claude.ai/code/routines), create a routine with this repo (or your fork) as the source and a daily schedule. For the prompt, use [`ROUTINE_PROMPT.md`](ROUTINE_PROMPT.md) with your URL, token and profile ([`profile.example.md`](profile.example.md)) filled in.
   - The cloud environment needs network access to `raw.githubusercontent.com` and `script.google.com` / `script.googleusercontent.com`.

## Tuning
- `JOB_WATCHER_PRIORITY_TERM` (`W27` or `S27`) and `JOB_WATCHER_PREFER_CANADA` (`1` or `0`) control the main boosts.
- For deeper changes, edit the regexes and `score()` in `job_watcher.py`, or `MAX_AGE_DAYS` for how far back each run looks.

Stdlib-only Python 3; nothing to install.
