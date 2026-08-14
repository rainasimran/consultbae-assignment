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

> **Note to self before submitting:** this log needs to reflect what
> *you* actually got stuck on while working through this, in your own
> words — not a generic writeup. The three points below are the real
> technical decision points this build hit; use them as a starting
> point, but rewrite them from your own hands-on experience before you
> submit, since you need to be able to defend every line live on a call.

**1. Matching people with no common ID across the 3 files.**
My first instinct was a simple two-pass join: match `source1`↔`source2`
on email, then match the result ↔`source3` on phone. That breaks the
moment a person exists in `source2` and `source3` but their `source1`
row got dropped for some reason, or more subtly, it doesn't naturally
generalize to "these two rows never share a key directly but both
connect through a third row." Switched to modeling it as a graph:
every raw row is a node, an edge exists wherever two rows share a
normalized email or phone, and connected components are the real
people. That's what let `Nikhil Chopra`'s two `source1` rows (different
emails, same phone) collapse into one person automatically instead of
needing a special case.
What I searched: "matching records across datasets without common key",
which led to "record linkage" and "union-find" as the actual terms for
this problem — I'd been thinking about it as a join problem when it's
closer to a clustering problem.
What I rejected: fuzzy name matching (e.g. `fuzzywuzzy`/Levenshtein
distance on names) as a *third* matching signal alongside email/phone.
It would have caught a few more real merges, but the `Arjun Mehta` case
(two different people, same name) is direct proof it would also
silently create false merges. Given the assignment scores "sensible
matching logic," a smaller number of *correct* merges seemed like the
right trade over more merges with unknown false-positive rate.

**2. No internet access to install librosa/pydub for audio analysis.**
The environment I built this in had no network access, so
`pip install librosa` / `pip install pydub` both failed outright — not
a "figure out the right version" problem, a "this approach is not
available here" problem. Checked what *was* available and found ffmpeg
already installed. Pivoted to calling `ffprobe`/`ffmpeg` directly via
`subprocess` — `ffprobe -show_format -show_streams` gives duration,
sample rate, and bitrate straight from the container metadata, and
`ffmpeg -af volumedetect` gives mean/max volume in dB, which is loudness
plus a decent free signal for the bonus noise/quality estimate (crest
factor — the gap between mean and peak volume — approximates dynamic
range; a flat, quiet signal is more likely to be background noise than
speech). In hindsight this is arguably the better call anyway: ffmpeg
is already on Render/Railway's images, whereas librosa drags in
numpy/scipy/numba for four numbers I could get from a subprocess call.
What I'd double check on a call: the noise-estimate thresholds
(`mean_db < -40` → noisy, `crest < 8` → moderate_noise) are hand-picked,
not derived from labeled data — I said so directly in
`audio_analysis.py`'s docstring rather than presenting them as more
rigorous than they are.

**3. The CTC column mixing absolute rupees and lakhs.**
Some `Current CTC` values are `417964`, others are `4.2` — at first
glance this looks like a rounding/formatting issue, but `4.2` rupees a
year makes no sense as a salary, so it's actually two different units in
one column. Went with a threshold heuristic (anything under 100 is
already lakhs) since no annual CTC in this dataset would plausibly be
under ₹100 in absolute rupees. This is a genuine assumption, not a
certain fact — logged every conversion so it's auditable
(`docs/DATA_ISSUES_AUTO.md` → `ctc_unit_mismatch`), and called it out as
an assumption in `docs/DATA_ISSUES.md` rather than presenting merged CTC
figures as more trustworthy than they are.
