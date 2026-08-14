# Task 5 — What breaks when 5,000 gig workers hit this over a weekend

Honest answer: this prototype (Flask dev server, SQLite, local disk for
audio files) breaks well before 5,000 submissions. Here's what fails
first, in the order I'd expect to hit it, and what I'd change.

## 1. The dev server itself (breaks almost immediately)

`app.run(debug=True)` is single-process, single-threaded by default. Two
people recording at the same time will queue behind each other. This is
the first wall, not the tenth.
**Fix before launch:** run behind gunicorn/uwsgi with multiple workers,
or on Render/Railway's actual production runner (not `flask run`).

## 2. SQLite write concurrency

SQLite allows one writer at a time; concurrent submissions from many
workers will start throwing `database is locked` errors well under
5,000 concurrent users, likely well under 100 concurrent *writes*.
**Fix:** move to Postgres before any real launch — this is a one-line
connection-string change since the schema is plain SQL with no
SQLite-specific syntax. I'd do this before 500 users, not wait for 5,000.

## 3. Local disk for audio files

Files saved to `app/static/audio/` on the app server's local disk:
- Render/Railway's filesystem is often ephemeral — a redeploy or restart
  can wipe everything already collected. This is a silent data-loss bug,
  the worst kind, because it works fine in every demo.
- Disk fills up: 5,000 workers × even a modest 1MB average clip = 5GB,
  fine for a free tier's ceiling but easy to blow past if people record
  multiple takes or upload longer files.
**Fix:** stream uploads directly to S3/Cloudflare R2/GCS instead of local
disk, store only the object key in the DB. Do this before launch, not
after — migrating files off a server that already lost some of them is
much worse than starting on object storage.

## 4. Duplicate submissions / retries

On a flaky mobile connection, "Submit" getting tapped twice, or a request
timing out and the user retrying, creates two full audio uploads with no
current dedup for `audio_submissions` (unlike `persons`, which does
dedupe). At scale this both wastes storage and pollutes any
downstream "which recordings are real" audit.
**Fix:** idempotency key from the client (e.g. a UUID generated in JS
before the request fires) checked server-side before accepting a second
upload with the same key within a short window.

## 5. ffmpeg/ffprobe subprocess calls under load

Audio analysis currently shells out to `ffprobe`/`ffmpeg` synchronously,
inside the request that's serving the upload. Under concurrent load this
means N simultaneous ffmpeg processes, each with real CPU/memory cost,
directly blocking the HTTP response. A burst of uploads (e.g. a
notification going out to all 5,000 workers at once, all uploading in
the first hour) would spike CPU and start timing out requests.
**Fix:** accept the upload, respond immediately, and do the ffmpeg
analysis in a background queue (Celery/RQ, or even just a lightweight
task queue) — decouple "file received" from "file analyzed."

## 6. Cost

- Bandwidth: 5,000 uploads × a few MB each is a few tens of GB — cheap on
  most platforms but worth checking the free-tier egress/ingress caps
  before assuming "free" stays free.
- Object storage + a real Postgres instance both have a floor cost once
  you're past free tiers — worth budgeting ~$20-50/mo minimum for a
  weekend-scale real launch rather than assuming the free stack holds.

## What I'd actually change before a real launch, in priority order

1. Postgres instead of SQLite (concurrency)
2. Object storage instead of local disk (durability — this is the one
   that can actually lose people's data, not just slow things down)
3. Background job queue for the ffmpeg analysis step (latency under load)
4. A real WSGI server + horizontal scaling (throughput)
5. Client-side idempotency keys (duplicate/retry hygiene)
