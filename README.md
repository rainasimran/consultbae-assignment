# ConsultBae AI Automation Assignment

## What's here

| Task | Where |
|---|---|
| 1 — Merge pipeline | `scripts/ingest.py`, `db/schema.sql` |
| 2 — n8n automation | `automation/n8n_duplicate_alert.json` |
| 3 — Audio app | `app/` (Flask) |
| 4 — Data issues report | `docs/DATA_ISSUES.md` (+ auto-generated `docs/DATA_ISSUES_AUTO.md`) |
| 5 — Stretch: scaling to 5,000 | `docs/STRETCH_SCALING.md` |

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# ffmpeg is required for Task 3's audio analysis - install it if you don't have it:
#   macOS: brew install ffmpeg   |   Ubuntu: sudo apt install ffmpeg

# Task 1: build the merged database from the 3 raw CSVs
python3 scripts/ingest.py
# -> writes db/consultbae.db, and docs/DATA_ISSUES_AUTO.md

# Task 3: run the audio app (reads/writes the same db/consultbae.db)
cd app && python3 app.py
# -> http://localhost:5000  (submit form)
# -> http://localhost:5000/submissions  (list view)
```

### Task 2 (n8n)

1. Run n8n (`npx n8n` locally, or n8n cloud trial).
2. Import `automation/n8n_duplicate_alert.json`.
3. In the "Send Duplicate Alert" node, replace the placeholder
   `https://webhook.site/REPLACE-WITH-YOUR-TEST-WEBHOOK-URL` with a real
   URL (webhook.site is the fastest way to get one for a demo).
4. Make sure `app/app.py` is running on `localhost:5000` (the workflow's
   "Check DB for Duplicate" node calls `/api/check_duplicate` there) — if
   n8n is running in Docker, `localhost` from inside the container won't
   reach your host; use `host.docker.internal` instead.
5. Activate the workflow, then POST to the webhook n8n gives you:
   ```bash
   curl -X POST <your-n8n-webhook-url> \
     -H "Content-Type: application/json" \
     -d '{"name": "Tanvi Gupta", "email": "tanvi.gupta31@example.com", "phone": "9000000254"}'
   ```
   This should trigger a duplicate alert (Tanvi Gupta is already in
   `source1`). Try a made-up name/email/phone and it should report
   `no_duplicate_new_applicant_accepted` instead.

## Design decisions (short version — full reasoning in `docs/DATA_ISSUES.md`)

- **Matching = union-find over normalized email/phone**, not name. Name
  matching was deliberately excluded after finding a planted case
  (`Arjun Mehta`) where the same name provably belongs to different
  people with different phone numbers — see `docs/DATA_ISSUES.md`.
- **Audio analysis uses ffprobe/ffmpeg directly**, not librosa/pydub —
  see stuck log below for why.
- **CTC and rate unit normalization are documented assumptions**, not
  facts extracted from the data — flagged explicitly rather than
  silently guessed.

## Stuck log

**1. Confusing the outer `consultbae-assignment` folder with the inner `consultbae` folder.**
When I opened a new terminal, commands like `cd app` or `.venv\Scripts\activate` kept failing
with "path does not exist" or "module could not be loaded". I ran `pwd` and `dir` to check
where I actually was, and realized [describe: you were one folder up / down from where you
thought]. Once I `cd`'d into the right folder, it worked.
What I searched: [what did you actually search, if anything?]

**2. Flask saying "No module named 'flask'" even though I'd already installed it.**
This happened because every time I opened a *new* terminal tab, the `.venv` virtual
environment wasn't automatically turned on in that new tab — it only stays active in the
tab where I ran `.venv\Scripts\activate`. I kept forgetting to reactivate it before running
`python app.py` in a fresh tab.
What fixed it: running `.venv\Scripts\activate` again in the new tab before trying to run
anything Python-related.

**3. `curl` failing with a weird "Cannot bind parameter 'Headers'" error.**
On Windows, typing `curl` in PowerShell doesn't run the real curl tool — it secretly runs
PowerShell's own `Invoke-WebRequest`, which doesn't understand `-H` and `-d` the same way.
What fixed it: switching to `Invoke-RestMethod` with PowerShell's own syntax instead of
trying to force curl-style commands to work.
