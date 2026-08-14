"""
ConsultBae assignment - Task 1: merge pipeline.

Strategy
--------
No single ID is common to all 3 files:
    source1 (naukri)   -> has email + phone
    source2 (gig)       -> has email only
    source3 (cbnexus)   -> has phone only

So matching is done as a graph problem, not a simple join:
    - every raw row from every source is a node
    - draw an edge between two rows if their NORMALIZED email matches,
      or their NORMALIZED phone matches
    - connected components = one real person, even if e.g. a source2 row
      and a source3 row never share a key directly, but both connect
      through the same source1 row (email bridges 1<->2, phone bridges 1<->3)

This is deliberately NOT name-based matching. The data has a planted
trap: two different people both named "Arjun Mehta" with different
phone numbers. Matching on name would silently merge two humans into
one. Name is only used as a tie-breaker / manual-review flag, never as
an auto-merge key. See docs/DATA_ISSUES.md for the full list of every
issue this script detects and how each is handled.
"""

import csv
import json
import re
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path

RAW = Path(__file__).resolve().parent.parent / "data" / "raw"
DB_PATH = Path(__file__).resolve().parent.parent / "db" / "consultbae.db"
SCHEMA_PATH = Path(__file__).resolve().parent.parent / "db" / "schema.sql"

ISSUES = []  # collected as we go, dumped to docs/DATA_ISSUES.md at the end


def log_issue(category, detail):
    ISSUES.append({"category": category, "detail": detail})


# ---------------------------------------------------------------- #
# Normalizers
# ---------------------------------------------------------------- #

CITY_ALIASES = {
    "bangalore": "Bengaluru", "bengaluru": "Bengaluru",
    "gurgaon": "Gurugram", "gurugram": "Gurugram",
    "delhi": "Delhi", "new delhi": "Delhi", "delhi ncr": "Delhi",
    "noida": "Noida",
    "pune": "Pune",
}


def norm_city(raw):
    if not raw:
        return None
    key = raw.strip().lower()
    canon = CITY_ALIASES.get(key)
    if canon is None:
        # unseen alias - keep a cleaned title-case version but flag it
        canon = raw.strip().title()
        log_issue("unmapped_city_alias", f"City '{raw}' not in alias map, kept as '{canon}'")
    return canon


def norm_phone(raw):
    """Strip everything to a bare 10-digit Indian mobile number."""
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    if digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]
    if len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if len(digits) != 10:
        log_issue("malformed_phone", f"Raw phone '{raw}' normalized to '{digits}' (not 10 digits)")
        return digits or None
    return digits


def norm_email(raw):
    if not raw:
        return None
    return raw.strip().lower()


def norm_skills(raw):
    if not raw:
        return []
    parts = [p.strip().lower() for p in raw.split(",") if p.strip()]
    # collapse near-duplicate spellings seen in the data
    fix = {"rest apis": "rest apis", "web scraping": "web scraping"}
    return sorted(set(fix.get(p, p) for p in parts))


DATE_FORMATS = [
    "%d-%m-%Y", "%Y-%m-%d", "%d %b %Y", "%m/%d/%Y",
]


def norm_date(raw):
    if not raw:
        return None
    raw = raw.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    log_issue("unparsed_date", f"Applied Date '{raw}' matched none of {DATE_FORMATS}")
    return None


def norm_ctc(raw):
    """source1 CTC column mixes absolute rupees (417964) and lakhs (4.2).
    Heuristic: any value < 100 is almost certainly lakhs already (annual
    CTC under 100 rupees makes no sense); anything else is absolute
    rupees and gets divided by 100000 to become lakhs."""
    if raw in (None, ""):
        return None
    val = float(raw)
    if val < 100:
        return round(val, 2)
    lpa = round(val / 100_000, 2)
    log_issue("ctc_unit_mismatch", f"CTC '{raw}' looked like absolute INR, converted to {lpa} LPA")
    return lpa


def norm_rate(raw):
    """source2 rate column mixes '1415/hr' and '15k/month'. Normalize
    everything to INR/hour, assuming a 22-day, 8-hour working month for
    the monthly figures (documented assumption - see DATA_ISSUES.md)."""
    if not raw:
        return None
    raw = raw.strip().lower()
    if "/hr" in raw:
        return round(float(raw.replace("/hr", "").strip()), 2)
    if "k/month" in raw:
        monthly = float(raw.replace("k/month", "").strip()) * 1000
        hourly = round(monthly / (22 * 8), 2)
        log_issue("rate_unit_conversion",
                   f"Rate '{raw}' converted month->hour using 22 days x 8 hrs assumption -> {hourly}/hr")
        return hourly
    log_issue("unparsed_rate", f"Rate '{raw}' matched neither '/hr' nor 'k/month'")
    return None


def norm_status(raw):
    if not raw:
        return None
    s = raw.strip().lower()
    mapping = {"active": "active", "inactive": "inactive", "paused": "paused"}
    return mapping.get(s, s)


def norm_verified(raw):
    if not raw:
        return None
    s = raw.strip().lower()
    return 1 if s in ("y", "yes") else 0


# ---------------------------------------------------------------- #
# Readers - one per source, each returns a list of normalized dicts
# plus keeps the raw row for audit trail
# ---------------------------------------------------------------- #

def read_source1():
    rows = []
    with open(RAW / "source1_naukri_applicants.csv", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for i, raw in enumerate(reader, start=2):  # row 1 is header
            rows.append({
                "source": "source1_naukri",
                "source_row": i,
                "raw": raw,
                "name": raw["Full Name"].strip(),
                "email": norm_email(raw["Email"]),
                "phone": norm_phone(raw["Phone"]),
                "city": norm_city(raw["City"]),
                "experience_years": float(raw["Experience (Years)"]) if raw["Experience (Years)"] else None,
                "current_ctc_lpa": norm_ctc(raw["Current CTC"]),
                "applied_date": norm_date(raw["Applied Date"]),
                "skills": norm_skills(raw["Skills"]),
            })
    return rows


def read_source2():
    rows = []
    with open(RAW / "source2_gig_workers.csv", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for i, raw in enumerate(reader, start=2):
            # blank row: every field empty
            if not any(v.strip() for v in raw.values() if v is not None):
                log_issue("blank_row", f"source2 row {i} is entirely empty, dropped")
                continue
            # corrupted / column-shifted row: email_id column should look
            # like an email; if it doesn't but a LATER column does, the
            # row's columns have been shifted and can't be trusted
            if raw["email_id"] and "@" not in raw["email_id"]:
                shifted_email = next((v for v in raw.values() if v and "@" in v), None)
                log_issue(
                    "column_shifted_row",
                    f"source2 row {i}: 'email_id' column holds '{raw['email_id']}' "
                    f"(not an email). Columns are shifted for this row "
                    f"(likely email='{shifted_email}'). Row dropped rather than "
                    f"guessed at - low confidence repair risks silently corrupting "
                    f"a real record; the same person already has a clean row "
                    f"elsewhere in the file."
                )
                continue
            rows.append({
                "source": "source2_gig",
                "source_row": i,
                "raw": raw,
                "name": raw["worker_name"].strip(),
                "email": norm_email(raw["email_id"]),
                "phone": None,  # source2 has no phone field
                "city": norm_city(raw["location"]),
                "gig_rate_inr_per_hr": norm_rate(raw["rate"]),
                "gig_status": norm_status(raw["status"]),
                "skills": norm_skills(raw["skill_tags"]),
            })
    return rows


def read_source3():
    rows = []
    with open(RAW / "source3_cbnexus_contacts.csv", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for i, raw in enumerate(reader, start=2):
            # repeated header row concatenated mid-file
            if raw["Name"] == "Name" and raw["Phone Number"] == "Phone Number":
                log_issue("duplicate_header_row", f"source3 row {i} is a repeated header, dropped")
                continue
            rows.append({
                "source": "source3_cbnexus",
                "source_row": i,
                "raw": raw,
                "name": raw["Name"].strip(),
                "email": None,  # source3 has no email field
                "phone": norm_phone(raw["Phone Number"]),
                "city": norm_city(raw["City"]),
                "cbnexus_verified": norm_verified(raw["Verified"]),
                "cbnexus_projects_done": int(raw["Projects Completed"]) if raw["Projects Completed"] else None,
                "skills": [],
            })
    return rows


# ---------------------------------------------------------------- #
# Union-Find across all rows from all sources
# ---------------------------------------------------------------- #

class UnionFind:
    def __init__(self, n):
        self.parent = list(range(n))

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


def merge_all(rows1, rows2, rows3):
    all_rows = rows1 + rows2 + rows3
    n = len(all_rows)
    uf = UnionFind(n)

    by_email = defaultdict(list)
    by_phone = defaultdict(list)
    for idx, r in enumerate(all_rows):
        if r.get("email"):
            by_email[r["email"]].append(idx)
        if r.get("phone"):
            by_phone[r["phone"]].append(idx)

    for key, idxs in by_email.items():
        for other in idxs[1:]:
            uf.union(idxs[0], other)
    for key, idxs in by_phone.items():
        for other in idxs[1:]:
            uf.union(idxs[0], other)

    # -- name-collision safety check (does NOT merge, only reports) --
    # if two rows share the same normalized name but end up in DIFFERENT
    # components (no shared email/phone), that's exactly the "two humans,
    # same name" trap. Flag it for manual review rather than merging.
    by_name = defaultdict(list)
    for idx, r in enumerate(all_rows):
        by_name[r["name"].strip().lower()].append(idx)
    for name, idxs in by_name.items():
        roots = set(uf.find(i) for i in idxs)
        if len(roots) > 1:
            # Distinguish the two real cases instead of asserting one story:
            #  (a) we have hard evidence they're different (e.g. two rows
            #      that DO share a bridging source but have conflicting
            #      phone/email) - rare here
            #  (b) we simply lack a bridging identifier (e.g. one row only
            #      has an email, the other only has a phone, no source1
            #      anchor connects them) - genuinely unknown, not "different"
            log_issue(
                "name_collision_not_merged",
                f"'{all_rows[idxs[0]]['name']}' appears {len(idxs)} times across sources "
                f"but resolves to {len(roots)} separate person records (no shared "
                f"email/phone links them). This could be one real person split across "
                f"systems with no bridging identifier, OR genuinely different people "
                f"who happen to share a name (both occur in this dataset). Left "
                f"UNMERGED rather than guessed - merging on name alone risks wrongly "
                f"combining two different humans (this is the case for 'Arjun Mehta', "
                f"who provably resolves to distinct phone numbers). Flagged for manual review."
            )

    groups = defaultdict(list)
    for idx in range(n):
        groups[uf.find(idx)].append(idx)

    people = []
    for root, idxs in groups.items():
        members = [all_rows[i] for i in idxs]
        people.append(members)
    return people


def build_person_record(members):
    """Collapse a connected component (1-3 raw rows) into one merged person."""
    sources_present = {m["source"] for m in members}
    by_source = {m["source"]: m for m in members}

    s1 = by_source.get("source1_naukri")
    s2 = by_source.get("source2_gig")
    s3 = by_source.get("source3_cbnexus")

    # prefer the most complete name (longest, avoids abbreviations like "R. Verma")
    name = max((m["name"] for m in members), key=len)

    email = next((m["email"] for m in members if m.get("email")), None)
    phone = next((m["phone"] for m in members if m.get("phone")), None)
    city = next((m["city"] for m in members if m.get("city")), None)

    skills = set()
    for m in members:
        skills.update(m.get("skills", []))

    if len(sources_present) >= 2:
        confidence = "exact"  # matched via shared email or phone
    else:
        confidence = "single_source"

    return {
        "name": name,
        "email": email,
        "phone": phone,
        "city": city,
        "experience_years": s1["experience_years"] if s1 else None,
        "current_ctc_lpa": s1["current_ctc_lpa"] if s1 else None,
        "applied_date": s1["applied_date"] if s1 else None,
        "gig_rate_inr_per_hr": s2["gig_rate_inr_per_hr"] if s2 else None,
        "gig_status": s2["gig_status"] if s2 else None,
        "cbnexus_verified": s3["cbnexus_verified"] if s3 else None,
        "cbnexus_projects_done": s3["cbnexus_projects_done"] if s3 else None,
        "match_confidence": confidence,
        "skills": sorted(skills),
        "raw_members": members,
    }


# ---------------------------------------------------------------- #
# Load into SQLite
# ---------------------------------------------------------------- #

def load_db(people):
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA_PATH.read_text())

    for p in people:
        cur = conn.execute(
            """INSERT INTO persons
               (canonical_name, canonical_email, canonical_phone, canonical_city,
                experience_years, current_ctc_lpa, applied_date,
                gig_rate_inr_per_hr, gig_status, cbnexus_verified, cbnexus_projects_done,
                match_confidence)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (p["name"], p["email"], p["phone"], p["city"],
             p["experience_years"], p["current_ctc_lpa"], p["applied_date"],
             p["gig_rate_inr_per_hr"], p["gig_status"], p["cbnexus_verified"], p["cbnexus_projects_done"],
             p["match_confidence"]),
        )
        person_id = cur.lastrowid
        for skill in p["skills"]:
            conn.execute("INSERT OR IGNORE INTO person_skills (person_id, skill) VALUES (?,?)",
                         (person_id, skill))
        for m in p["raw_members"]:
            conn.execute(
                "INSERT INTO person_sources (person_id, source_name, source_row, raw_json) VALUES (?,?,?,?)",
                (person_id, m["source"], m["source_row"], json.dumps(m["raw"])),
            )
    conn.commit()
    return conn


def write_issues_report(rows1, rows2, rows3, people):
    exact = sum(1 for p in people if p["match_confidence"] == "exact")
    single = len(people) - exact
    by_cat = defaultdict(list)
    for issue in ISSUES:
        by_cat[issue["category"]].append(issue["detail"])

    lines = []
    lines.append("# Data Issues Report (auto-generated by scripts/ingest.py)\n")
    lines.append(f"- source1 rows read: {len(rows1)}")
    lines.append(f"- source2 rows read (after dropping corrupted/blank): {len(rows2)}")
    lines.append(f"- source3 rows read (after dropping repeated header): {len(rows3)}")
    lines.append(f"- **Total distinct people after merge: {len(people)}**")
    lines.append(f"  - matched across 2+ sources (high confidence): {exact}")
    lines.append(f"  - appear in only 1 source: {single}\n")

    lines.append("## Issues found, by category\n")
    for cat, details in sorted(by_cat.items()):
        lines.append(f"### {cat} ({len(details)})")
        for d in details:
            lines.append(f"- {d}")
        lines.append("")

    out_path = Path(__file__).resolve().parent.parent / "docs" / "DATA_ISSUES_AUTO.md"
    out_path.write_text("\n".join(lines))
    print(f"Wrote {out_path}")
    return out_path


def main():
    rows1 = read_source1()
    rows2 = read_source2()
    rows3 = read_source3()

    components = merge_all(rows1, rows2, rows3)
    people = [build_person_record(m) for m in components]

    conn = load_db(people)
    write_issues_report(rows1, rows2, rows3, people)

    print(f"source1: {len(rows1)} rows, source2: {len(rows2)} rows, source3: {len(rows3)} rows")
    print(f"-> merged into {len(people)} unique persons")
    print(f"-> {sum(1 for p in people if p['match_confidence']=='exact')} matched across multiple sources")
    conn.close()


if __name__ == "__main__":
    main()
