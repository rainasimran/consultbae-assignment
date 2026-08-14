import re
import sqlite3
import sys
import uuid
from pathlib import Path

from flask import Flask, g, render_template, request, redirect, url_for, send_from_directory, jsonify

sys.path.insert(0, str(Path(__file__).resolve().parent))
from audio_analysis import analyze_audio

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "db" / "consultbae.db"
AUDIO_DIR = Path(__file__).resolve().parent / "static" / "audio"
AUDIO_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_EXT = {".webm", ".wav", ".mp3", ".m4a", ".ogg"}

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024  # 25MB cap per submission


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def norm_phone(raw):
    """Same normalization rule as scripts/ingest.py, kept in sync manually -
    see README stuck log for why this isn't a shared module yet."""
    digits = re.sub(r"\D", "", raw or "")
    if digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]
    if len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    return digits


def find_or_create_person(db, name, phone):
    """Link an audio submission to an existing merged person if the
    normalized phone matches, otherwise create a new single-source person
    so the submission still has a valid person_id per Task 3's requirement."""
    row = db.execute(
        "SELECT person_id FROM persons WHERE canonical_phone = ?", (phone,)
    ).fetchone()
    if row:
        return row["person_id"]

    cur = db.execute(
        """INSERT INTO persons (canonical_name, canonical_phone, match_confidence)
           VALUES (?, ?, 'single_source')""",
        (name, phone),
    )
    person_id = cur.lastrowid
    db.execute(
        "INSERT INTO person_sources (person_id, source_name, source_row, raw_json) VALUES (?,?,?,?)",
        (person_id, "audio_app", 0, f'{{"name": "{name}", "phone": "{phone}"}}'),
    )
    db.commit()
    return person_id


@app.route("/")
def index():
    return render_template("submit.html")


@app.route("/submit", methods=["POST"])
def submit():
    name = request.form.get("name", "").strip()
    phone_raw = request.form.get("phone", "").strip()
    audio_file = request.files.get("audio")

    if not name or not phone_raw or not audio_file or audio_file.filename == "":
        return render_template(
            "submit.html", error="Name, phone, and an audio recording/file are all required."
        ), 400

    phone = norm_phone(phone_raw)
    if len(phone) != 10:
        return render_template(
            "submit.html", error=f"Phone number '{phone_raw}' doesn't look like a valid 10-digit number."
        ), 400

    ext = Path(audio_file.filename).suffix.lower() or ".webm"
    if ext not in ALLOWED_EXT:
        ext = ".webm"  # browser MediaRecorder blobs often arrive without a proper filename/ext

    fname = f"{uuid.uuid4().hex}{ext}"
    fpath = AUDIO_DIR / fname
    audio_file.save(fpath)

    analysis = analyze_audio(str(fpath))

    db = get_db()
    person_id = find_or_create_person(db, name, phone)

    db.execute(
        """INSERT INTO audio_submissions
           (person_id, name, phone, file_path, duration_sec, sample_rate_hz,
            bitrate_kbps, loudness_dbfs, noise_estimate)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (person_id, name, phone, f"audio/{fname}",
         analysis["duration_sec"], analysis["sample_rate_hz"],
         analysis["bitrate_kbps"], analysis["loudness_dbfs"], analysis["noise_estimate"]),
    )
    db.commit()

    return redirect(url_for("submissions"))


@app.route("/submissions")
def submissions():
    db = get_db()
    rows = db.execute(
        "SELECT * FROM audio_submissions ORDER BY submitted_at DESC"
    ).fetchall()
    return render_template("submissions.html", rows=rows)


@app.route("/api/check_duplicate")
def api_check_duplicate():
    """Task 2 support endpoint: used by the n8n workflow (automation/n8n_duplicate_alert.json).
    Given an email and/or phone (raw, unnormalized - same messiness as the
    source CSVs), reports whether a matching person already exists.
    Uses the exact same normalization rules as scripts/ingest.py so a new
    row gets matched the same way it would have during the original merge."""
    email_raw = request.args.get("email", "")
    phone_raw = request.args.get("phone", "")
    email = email_raw.strip().lower() if email_raw else None
    phone = norm_phone(phone_raw) if phone_raw else None

    db = get_db()
    match = None
    matched_via = None
    if email:
        match = db.execute("SELECT * FROM persons WHERE canonical_email = ?", (email,)).fetchone()
        if match:
            matched_via = "email"
    if not match and phone:
        match = db.execute("SELECT * FROM persons WHERE canonical_phone = ?", (phone,)).fetchone()
        if match:
            matched_via = "phone"

    if match:
        return jsonify({
            "is_duplicate": True,
            "matched_via": matched_via,
            "person_id": match["person_id"],
            "canonical_name": match["canonical_name"],
            "canonical_email": match["canonical_email"],
            "canonical_phone": match["canonical_phone"],
        })
    return jsonify({"is_duplicate": False})


@app.route("/static/audio/<path:filename>")
def serve_audio(filename):
    return send_from_directory(AUDIO_DIR, filename)


if __name__ == "__main__":
    if not DB_PATH.exists():
        print(f"WARNING: {DB_PATH} not found - run scripts/ingest.py first (Task 1).")
    app.run(debug=True, host="0.0.0.0", port=5000)
