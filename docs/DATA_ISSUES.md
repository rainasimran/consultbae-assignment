# Task 4 — Data Issues Report

This report is hand-written but every count/example in it is backed by
`docs/DATA_ISSUES_AUTO.md`, which `scripts/ingest.py` regenerates every run
directly from the data (not from memory). Re-run `python3 scripts/ingest.py`
and diff that file if you want to verify any number here.

## Summary

| | count |
|---|---|
| Raw rows across all 3 files | 105 |
| Rows dropped as corrupted/blank/header noise | 3 |
| Unique people after merge | 60 |
| People matched across 2+ sources (high confidence) | 25 |
| People appearing in only 1 source | 35 |

## No common ID across files

`source1` has email+phone, `source2` has email only, `source3` has phone
only. There is no column that exists in all three. **Decision:** treat
matching as a graph problem — union rows that share a normalized email OR
a normalized phone, take connected components as "one person." This lets
a `source2` row and a `source3` row merge correctly even when they don't
share a key directly, as long as a `source1` row bridges both. Name is
**never** used to auto-merge (see the collision issue below for why).

## Formatting inconsistencies (normalized before matching, or matching would silently fail)

- **Phone numbers**, four formats in the same column: `+919000000254`,
  `9000000237`, `09000000287` (leading 0), `919000000260` (no `+`). All
  normalized to a bare 10-digit number.
- **City names / aliases**: `Gurgaon` vs `Gurugram`, `Bangalore` vs
  `Bengaluru`, `Delhi` vs `New Delhi` vs `Delhi NCR` are the same place
  under different strings, plus casing (`NOIDA`/`Noida`/`noida`) and
  trailing whitespace (`"Noida "`, `"gurugram "`). Mapped through an
  explicit alias table (`CITY_ALIASES` in `ingest.py`) rather than fuzzy
  string matching, since fuzzy matching on short city names risks false
  merges (e.g. "Noida" vs "Noida " is safe, but auto-fuzzing arbitrary
  strings is not).
- **Applied Date**, four different formats in one column: `24-07-2026`,
  `2026-08-08`, `7 Jul 2026`, `07/13/2026`. Parsed with a format list,
  tried in order, and normalized to ISO `YYYY-MM-DD`.
- **Email casing** in `source2`: some rows are `ISHA.CHOPRA95@MAILTEST...`,
  others lowercase. Normalized to lowercase before matching — this alone
  would have silently broken the email-based merge for several people if
  skipped.
- **Status/Verified enums**: `source2.status` has `Active/active/ACTIVE`
  and also `paused`, which isn't a case variant of `Active`/`Inactive` —
  it's a genuinely separate state, kept as its own value rather than
  folded into `Inactive`. `source3.Verified` mixes `Y/N` and `yes/No`,
  normalized to 0/1.

## Unit inconsistencies (not just formatting — the *numbers mean different things*)

- **`source1.Current CTC`** mixes absolute rupees (`417964`) with
  decimals that are clearly lakhs already (`4.2`, `8.3`, `11.2` — no one's
  annual CTC is ₹4.20). **Decision:** any value under 100 is treated as
  already-in-lakhs; anything else is divided by 100,000. This is a
  heuristic, not a certainty — flagged as an assumption in the README, and
  every conversion is logged (21 rows affected, see
  `docs/DATA_ISSUES_AUTO.md` → `ctc_unit_mismatch`).
- **`source2.rate`** mixes hourly (`1415/hr`) and monthly (`15k/month`)
  figures. **Decision:** normalize everything to INR/hour using an assumed
  22 working days × 8 hours/month — an explicit, documented assumption,
  not a fact in the data. A recruiter comparing rates across the merged
  table without this normalization would be comparing apples to oranges.

## Structural corruption (not just messy values — malformed rows)

- **`source2` row 12 is entirely blank** (`,,,,,`) — dropped.
- **`source2` row 20 has shifted columns**: the `email_id` column holds
  `"react, javascript, mysql"` — a skill list, not an email. The columns
  appear to have rotated by one position for this single row (skill_tags
  bleeding into email_id). The real email is visible in a later column.
  **Decision: dropped rather than auto-repaired.** The same person (Isha
  Chopra) already has a clean, correctly-formatted row elsewhere in the
  file, so guessing at a column-shift repair only adds risk (misaligning
  a *different* row by mistake) for zero information gain.
- **`source3` has a second header row concatenated mid-file** (row 16
  repeats `Name,Phone Number,City,Verified,Projects Completed` as data) —
  looks like two exports were `cat`'d together without stripping the
  second header. Detected and dropped.

## Duplicate / near-duplicate records

- **Exact duplicate row within `source1`**: `"R. Verma"` and `"Rohit
  Verma"` share the identical email and phone and every other field —
  same application submitted twice, one with an abbreviated first name.
  Collapsed into one person automatically (the union-find matches them
  via shared email+phone regardless of the name string); the fuller name
  is kept as the canonical display name.
- **`Nikhil Chopra` appears twice in `source1`** with two *different*
  emails (`alt.nikhil.chopra70@...` and `nikhil.chopra70@...`) but the
  identical phone number and every other field. Same person, most likely
  applied twice with a backup email. Merged via the shared phone.

## The trap: name collisions that are NOT the same person

This is the one that matters most for matching logic, and the reason
matching is **not** done on name:

- **`Arjun Mehta`** appears 4 times across the files but resolves to
  **3 different graph components** — one pair genuinely shares a phone
  number (source1 ↔ source3, matched correctly), but the other two
  occurrences have distinct, non-matching phone numbers. At least two of
  the four rows are provably different humans who happen to share a
  common Indian name. A name-based or fuzzy-name matcher would have
  silently merged all four into one fabricated "super-person" with a
  blended CTC, rate, and skill list that belongs to nobody.
- The same ambiguity shows up for `Deepak Nair`, `Manish Bhatia`, `Divya
  Chopra`, `Karan Chopra`, and `Vikram Mehta` — each appears more than
  once with no shared email/phone bridging the occurrences. **Decision:**
  these are left as separate person records and flagged in
  `docs/DATA_ISSUES_AUTO.md` under `name_collision_not_merged`, honestly
  labeled as "could be one person split across systems, or could be two
  different people" — the data genuinely doesn't say which, and a merge
  pipeline that guesses wrong here silently corrupts real records.

## What was *not* done (scope decisions, not oversights)

- No fuzzy/Levenshtein name matching was added as a matching signal.
  Given the collision trap above, adding it would trade a few more
  correct merges for a real risk of wrong merges — a bad trade for a
  system whose output (a merged people database) other automations build
  on top of.
- Skills were normalized by lowercasing/trimming only, not semantically
  deduplicated (e.g. `"rest apis"` vs `"REST APIs"` collapse, but no
  attempt to decide if `"web dev"` and `"react"` should be grouped — that
  is a modeling decision for Task 2's tagging step, not a data-cleaning one).
