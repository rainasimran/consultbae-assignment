-- ConsultBae assignment: merged schema
-- Design notes (see README for full reasoning):
--   - `persons` is the deduped, one-row-per-human table. Nothing here is
--     "raw" — every value has been normalized (phone, email, city, dates).
--   - `person_sources` keeps a row per ORIGINAL record that fed into a
--     person, with the raw fields untouched, so merges are always
--     auditable / reversible. Never throw away the raw row.
--   - `person_skills` is a normalized join table (skills were a messy
--     comma string in the source files with inconsistent casing/naming).
--   - `audio_submissions` is Task 3's table, FK'd to persons so an audio
--     submission always resolves to a real (deduped) person record.

PRAGMA foreign_keys = ON;

CREATE TABLE persons (
    person_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_name       TEXT NOT NULL,
    canonical_email      TEXT,              -- normalized, lowercased, may be NULL (source3 has no email)
    canonical_phone      TEXT,              -- normalized to 10-digit national number, may be NULL
    canonical_city        TEXT,              -- normalized against alias map
    -- fields below are "best available" merged values, source noted in person_sources
    experience_years     REAL,               -- from source1
    current_ctc_lpa       REAL,               -- from source1, normalized to Lakhs Per Annum
    applied_date          TEXT,               -- from source1, normalized ISO 'YYYY-MM-DD'
    gig_rate_inr_per_hr    REAL,               -- from source2, normalized to INR/hour
    gig_status            TEXT,               -- from source2, normalized enum
    cbnexus_verified      INTEGER,            -- from source3, normalized 0/1
    cbnexus_projects_done  INTEGER,            -- from source3
    match_confidence      TEXT NOT NULL,      -- 'exact' (email or phone match) | 'single_source' | 'manual_review'
    created_at            TEXT DEFAULT (datetime('now'))
);

CREATE TABLE person_sources (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id     INTEGER NOT NULL REFERENCES persons(person_id),
    source_name   TEXT NOT NULL,       -- 'source1_naukri' | 'source2_gig' | 'source3_cbnexus'
    source_row    INTEGER NOT NULL,    -- 1-indexed row number in the ORIGINAL csv (for auditing)
    raw_json      TEXT NOT NULL        -- the untouched original row, as JSON
);

CREATE TABLE person_skills (
    person_id   INTEGER NOT NULL REFERENCES persons(person_id),
    skill       TEXT NOT NULL,          -- normalized (lowercased, trimmed) skill tag
    PRIMARY KEY (person_id, skill)
);

CREATE TABLE audio_submissions (
    submission_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    person_id      INTEGER REFERENCES persons(person_id),  -- nullable: new submitter not yet in persons
    name           TEXT NOT NULL,
    phone          TEXT NOT NULL,
    file_path      TEXT NOT NULL,
    duration_sec   REAL,
    sample_rate_hz INTEGER,
    bitrate_kbps   REAL,
    loudness_dbfs  REAL,
    noise_estimate TEXT,               -- rough quality bucket: 'clean' | 'moderate_noise' | 'noisy'
    submitted_at   TEXT DEFAULT (datetime('now'))
);

CREATE INDEX idx_persons_email ON persons(canonical_email);
CREATE INDEX idx_persons_phone ON persons(canonical_phone);
