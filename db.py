"""
db.py — CareFlow AI SQLite database
Tables, helpers, permission checks (require_role, owns_case).
All queries use parameterised statements — no string formatting with user data.
"""

import sqlite3
import json
import os
import hashlib
import secrets
import uuid
from typing import Optional
from contextlib import contextmanager

DB_PATH = os.path.join(os.path.dirname(__file__), "careflow.db")
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")


def resolve_intake_path(path: str | None, case_id: str | None = None) -> str:
    """Resolve a stored intake file path to a file that exists on this machine.

    Demo databases are sometimes copied between machines, so a case can store
    an absolute path such as ``/Users/ash/.../voice.mp3`` while the same file
    lives under ``data/demo_cases/cardiac_hinglish/voice.mp3`` here. This helper
    rewrites such stale paths by basename, preferring the case's own upload /
    demo folder.

    Returns the original path when no candidate exists so callers can still
    show a clear "file not found" message.
    """
    if not path:
        return path
    if os.path.exists(path):
        return path

    base = os.path.basename(path.replace("\\", "/"))
    candidates: list[str] = []

    if case_id:
        candidates.append(os.path.join(DATA_DIR, "uploads", case_id, base))
        if case_id.startswith("demo_"):
            candidates.append(
                os.path.join(DATA_DIR, "demo_cases", case_id[len("demo_"):], base)
            )

    for root_name in ("demo_cases", "uploads"):
        root = os.path.join(DATA_DIR, root_name)
        if not os.path.isdir(root):
            continue
        matches = []
        for dirpath, _dirnames, filenames in os.walk(root):
            if base in filenames:
                matches.append(os.path.join(dirpath, base))
        # Only accept an unambiguous match; several demo cases share report.png.
        if len(matches) == 1:
            candidates.append(matches[0])

    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    return path

# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------
VALID_ROLES = {"patient", "nurse", "doctor", "admin"}

# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class PermissionDenied(Exception):
    """Raised when a role tries to perform a forbidden action."""

class ConsentRequired(Exception):
    """Raised when an intake function is called without consent."""

class CaseNotFound(Exception):
    """Raised when a case_id doesn't exist."""


# ---------------------------------------------------------------------------
# Connection helper
# ---------------------------------------------------------------------------

@contextmanager
def get_conn(path: str = DB_PATH):
    """Yield a SQLite connection with row_factory = Row."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Schema initialisation
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id            TEXT PRIMARY KEY,
    email         TEXT UNIQUE,
    password_hash TEXT,
    name          TEXT NOT NULL,
    role          TEXT NOT NULL CHECK(role IN ('patient','nurse','doctor','admin'))
);

CREATE TABLE IF NOT EXISTS cases (
    id                  TEXT PRIMARY KEY,
    pseudonym_code      TEXT NOT NULL UNIQUE,
    created_by          TEXT NOT NULL REFERENCES users(id),
    consent_status      TEXT NOT NULL DEFAULT 'pending' CHECK(consent_status IN ('pending','given','withdrawn')),
    consent_timestamp   TEXT,
    consent_language    TEXT,
    consent_text_hash   TEXT,
    consented_by        TEXT,    -- user id who gave consent (may differ for nurse-on-behalf)
    status              TEXT NOT NULL DEFAULT 'intake' CHECK(status IN ('intake','processing','ready','signed_off','erased')),
    final_urgency       TEXT,    -- NULL until clinician signs off
    age_band            TEXT,    -- optional, e.g. "30-40"
    sex                 TEXT,    -- optional
    submitted_at        TEXT
);

CREATE TABLE IF NOT EXISTS intakes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id     TEXT NOT NULL REFERENCES cases(id),
    channel     TEXT NOT NULL CHECK(channel IN ('text','voice','ocr','vitals')),
    content_ref TEXT,            -- file path for voice/ocr; JSON for vitals; NULL for text
    content_text TEXT,           -- inline text for text channel
    question    TEXT,            -- follow-up question answered by this text intake (optional)
    msg_index   INTEGER,         -- for text channel: MSG_n index
    ocr_line_start INTEGER,      -- for ocr: first line index in this upload
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS ai_outputs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id         TEXT NOT NULL REFERENCES cases(id),
    version         INTEGER NOT NULL DEFAULT 1,
    output_json     TEXT NOT NULL,    -- PipelineResult as JSON
    suggested_urgency TEXT NOT NULL,
    latency_ms      REAL,
    tokens_in       INTEGER,
    tokens_out      INTEGER,
    model           TEXT,
    cached          INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS clinician_actions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id     TEXT NOT NULL REFERENCES cases(id),
    actor       TEXT NOT NULL REFERENCES users(id),
    action      TEXT NOT NULL CHECK(action IN ('approve','edit_approve','override','note')),
    new_urgency TEXT,            -- set if action is override
    reason      TEXT,            -- required for override
    edited_note TEXT,            -- set if action is edit_approve
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS audit_logs (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL DEFAULT (datetime('now')),
    actor_role  TEXT NOT NULL,
    action_type TEXT NOT NULL,
    case_hash   TEXT,            -- HMAC of case_id, no PHI
    details_json TEXT NOT NULL DEFAULT '{}',
    confidence  REAL,
    prev_hash   TEXT NOT NULL,
    hash        TEXT NOT NULL
);

-- Append-only trigger: prevent UPDATE/DELETE on audit_logs
CREATE TRIGGER IF NOT EXISTS audit_no_update
BEFORE UPDATE ON audit_logs
BEGIN
    SELECT RAISE(ABORT, 'audit_logs is append-only: UPDATE not permitted');
END;

CREATE TRIGGER IF NOT EXISTS audit_no_delete
BEFORE DELETE ON audit_logs
BEGIN
    SELECT RAISE(ABORT, 'audit_logs is append-only: DELETE not permitted');
END;
"""


def hash_password(password: str) -> str:
    """Generate salted PBKDF2 hash of password."""
    salt = secrets.token_hex(16)
    key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 100000).hex()
    return f"{salt}${key}"


def verify_password(password: str, stored_hash: str) -> bool:
    """Verify password against stored salted PBKDF2 hash."""
    if not stored_hash or "$" not in stored_hash:
        return False
    try:
        salt, key = stored_hash.split("$", 1)
        calc_key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 100000).hex()
        return secrets.compare_digest(key, calc_key)
    except Exception:
        return False


def seed_default_users(db_path: str = DB_PATH) -> None:
    """Ensure standard demo accounts are always available with known credentials."""
    defaults = [
        ("doctor_sharma", "Dr. Sharma", "doctor@hospital.org", "doctor123", "doctor"),
        ("nurse_sunita", "Sunita Sharma", "nurse@hospital.org", "nurse123", "nurse"),
        ("patient_demo", "Priya Patel", "patient@careflow.ai", "patient123", "patient"),
        ("admin_user", "System Admin", "admin@hospital.org", "admin123", "admin"),
    ]
    with get_conn(db_path) as conn:
        for uid, name, email, pwd, role in defaults:
            row = conn.execute("SELECT id, password_hash FROM users WHERE id = ?", (uid,)).fetchone()
            if not row:
                pwd_hash = hash_password(pwd)
                conn.execute(
                    """INSERT OR REPLACE INTO users (id, name, email, password_hash, role)
                       VALUES (?, ?, ?, ?, ?)""",
                    (uid, name, email, pwd_hash, role),
                )
            elif not row["password_hash"]:
                pwd_hash = hash_password(pwd)
                conn.execute(
                    "UPDATE users SET password_hash = ?, email = ? WHERE id = ?",
                    (pwd_hash, email, uid),
                )


def init_db(path: str = DB_PATH) -> None:
    """Create all tables, triggers, and ensure schema migrations."""
    with get_conn(path) as conn:
        conn.executescript(SCHEMA_SQL)
        # Migration: ensure email and password_hash exist on older SQLite databases
        cur = conn.execute("PRAGMA table_info(users)")
        cols = {row["name"] for row in cur.fetchall()}
        if "email" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN email TEXT")
        if "password_hash" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN password_hash TEXT")

        # Migration: ensure patient_name exists on cases
        cur_cases = conn.execute("PRAGMA table_info(cases)")
        cases_cols = {row["name"] for row in cur_cases.fetchall()}
        if "patient_name" not in cases_cols:
            conn.execute("ALTER TABLE cases ADD COLUMN patient_name TEXT")
        conn.execute("UPDATE cases SET patient_name = COALESCE((SELECT name FROM users WHERE id = cases.created_by), 'Anonymous Patient') WHERE patient_name IS NULL")

        # Migration: store the follow-up question that a text answer responds to
        cur_intakes = conn.execute("PRAGMA table_info(intakes)")
        intake_cols = {row["name"] for row in cur_intakes.fetchall()}
        if "question" not in intake_cols:
            conn.execute("ALTER TABLE intakes ADD COLUMN question TEXT")
    seed_default_users(path)


# ---------------------------------------------------------------------------
# Permission helpers
# ---------------------------------------------------------------------------

def require_role(actor_id: str, allowed_roles: set[str], db_path: str = DB_PATH) -> dict:
    """
    Return the user record if actor_id has a role in allowed_roles.
    Raise PermissionDenied otherwise.
    """
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT id, name, role FROM users WHERE id = ?", (actor_id,)
        ).fetchone()
    if row is None:
        raise PermissionDenied(f"Unknown user: {actor_id}")
    if row["role"] not in allowed_roles:
        raise PermissionDenied(
            f"User {actor_id} has role '{row['role']}', need one of {allowed_roles}"
        )
    return dict(row)


def owns_case(actor_id: str, case_id: str, db_path: str = DB_PATH) -> bool:
    """Return True if actor_id created this case."""
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT created_by FROM cases WHERE id = ?", (case_id,)
        ).fetchone()
    if row is None:
        raise CaseNotFound(f"Case {case_id} not found")
    return row["created_by"] == actor_id


# ---------------------------------------------------------------------------
# User helpers
# ---------------------------------------------------------------------------

def upsert_user(
    user_id: str,
    name: str,
    role: str,
    db_path: str = DB_PATH,
    email: Optional[str] = None,
    password_hash: Optional[str] = None,
) -> None:
    """Insert or update a user."""
    if role not in VALID_ROLES:
        raise ValueError(f"Invalid role: {role}")
    with get_conn(db_path) as conn:
        conn.execute(
            """INSERT INTO users (id, name, email, password_hash, role)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                   name = excluded.name,
                   role = excluded.role,
                   email = COALESCE(excluded.email, users.email),
                   password_hash = COALESCE(excluded.password_hash, users.password_hash)""",
            (user_id, name, email, password_hash, role),
        )


def register_user(
    name: str,
    email: str,
    password: str,
    role: str,
    db_path: str = DB_PATH,
) -> dict:
    """Register a new user with email and hashed password."""
    email_clean = email.strip().lower()
    if not email_clean or "@" not in email_clean:
        raise ValueError("A valid email address is required.")
    if len(password) < 6:
        raise ValueError("Password must be at least 6 characters.")
    if role not in VALID_ROLES:
        raise ValueError(f"Invalid role: {role}. Must be one of {VALID_ROLES}")

    pwd_hash = hash_password(password)
    user_id = f"{role}_{uuid.uuid4().hex[:8]}"

    with get_conn(db_path) as conn:
        existing = conn.execute(
            "SELECT id FROM users WHERE LOWER(email) = ?", (email_clean,)
        ).fetchone()
        if existing:
            raise ValueError(f"An account with email '{email_clean}' already exists.")

        conn.execute(
            """INSERT INTO users (id, name, email, password_hash, role)
               VALUES (?, ?, ?, ?, ?)""",
            (user_id, name.strip(), email_clean, pwd_hash, role),
        )

    return {"id": user_id, "name": name.strip(), "email": email_clean, "role": role}


def authenticate_user(
    email: str,
    password: str,
    db_path: str = DB_PATH,
) -> Optional[dict]:
    """Authenticate a user by email and password. Returns user dict or None."""
    email_clean = email.strip().lower()
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT id, name, email, password_hash, role FROM users WHERE LOWER(email) = ?",
            (email_clean,),
        ).fetchone()

    if not row or not row["password_hash"]:
        return None

    if verify_password(password, row["password_hash"]):
        return {
            "id": row["id"],
            "name": row["name"],
            "email": row["email"],
            "role": row["role"],
        }
    return None


def get_user(user_id: str, db_path: str = DB_PATH) -> Optional[dict]:
    """Return user dict or None."""
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT id, name, email, role FROM users WHERE id = ?", (user_id,)
        ).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Case helpers
# ---------------------------------------------------------------------------

def create_case(
    case_id: str,
    pseudonym_code: str,
    created_by: str,
    age_band: Optional[str] = None,
    sex: Optional[str] = None,
    patient_name: Optional[str] = None,
    db_path: str = DB_PATH,
) -> None:
    """Create a new case record (no consent yet)."""
    with get_conn(db_path) as conn:
        conn.execute(
            """INSERT INTO cases (id, pseudonym_code, created_by, age_band, sex, patient_name)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (case_id, pseudonym_code, created_by, age_band, sex, patient_name),
        )


def get_case(case_id: str, db_path: str = DB_PATH) -> Optional[dict]:
    """Return case dict with creator and patient metadata or None."""
    with get_conn(db_path) as conn:
        row = conn.execute(
            """SELECT c.*,
                      COALESCE(c.patient_name, u.name, 'Anonymous Patient') as patient_name,
                      COALESCE(u.name, c.created_by, 'System') as creator_name
               FROM cases c LEFT JOIN users u ON c.created_by = u.id
               WHERE c.id = ?""",
            (case_id,),
        ).fetchone()
    return dict(row) if row else None


def update_case_status(
    case_id: str, status: str, db_path: str = DB_PATH
) -> None:
    with get_conn(db_path) as conn:
        conn.execute(
            "UPDATE cases SET status = ? WHERE id = ?", (status, case_id)
        )


def set_final_urgency(
    case_id: str,
    urgency: str,
    db_path: str = DB_PATH,
) -> None:
    """Set final_urgency — only called after clinician sign-off."""
    with get_conn(db_path) as conn:
        conn.execute(
            "UPDATE cases SET final_urgency = ?, status = 'signed_off' WHERE id = ?",
            (urgency, case_id),
        )


def list_cases_for_queue(db_path: str = DB_PATH) -> list[dict]:
    """Return all non-erased cases for the clinician queue."""
    with get_conn(db_path) as conn:
        rows = conn.execute(
            """SELECT c.*,
               COALESCE(c.patient_name, u.name, 'Anonymous Patient') as patient_name,
               COALESCE(u.name, c.created_by, 'Patient') as creator_name,
               (SELECT suggested_urgency FROM ai_outputs WHERE case_id=c.id ORDER BY version DESC LIMIT 1) as ai_urgency,
               (SELECT cached FROM ai_outputs WHERE case_id=c.id ORDER BY version DESC LIMIT 1) as ai_cached,
               (SELECT output_json FROM ai_outputs WHERE case_id=c.id ORDER BY version DESC LIMIT 1) as output_json,
               (SELECT content_text FROM intakes WHERE case_id=c.id AND channel='text' ORDER BY id ASC LIMIT 1) as raw_symptom_text,
               EXISTS(SELECT 1 FROM intakes WHERE case_id=c.id AND channel='ocr') as has_report
               FROM cases c LEFT JOIN users u ON c.created_by = u.id
               WHERE c.status != 'erased' AND c.submitted_at IS NOT NULL
               ORDER BY c.submitted_at DESC""",
            ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Intake helpers
# ---------------------------------------------------------------------------

def add_intake(
    case_id: str,
    channel: str,
    content_text: Optional[str] = None,
    content_ref: Optional[str] = None,
    msg_index: Optional[int] = None,
    ocr_line_start: Optional[int] = None,
    question: Optional[str] = None,
    db_path: str = DB_PATH,
) -> int:
    """Add an intake row. Returns the row id.

    ``question`` is only used for text intakes that answer a follow-up
    question, so clinicians can see what the patient was answering.
    """
    with get_conn(db_path) as conn:
        cur = conn.execute(
            """INSERT INTO intakes
               (case_id, channel, content_text, content_ref, msg_index, ocr_line_start, question)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (case_id, channel, content_text, content_ref, msg_index, ocr_line_start, question),
        )
        return cur.lastrowid


def get_intakes(case_id: str, db_path: str = DB_PATH) -> list[dict]:
    """Return all intakes for a case, ordered by creation time."""
    with get_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM intakes WHERE case_id = ? ORDER BY created_at, id",
            (case_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def next_msg_index(case_id: str, db_path: str = DB_PATH) -> int:
    """Return the next MSG_n index for text intakes."""
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT MAX(msg_index) as mx FROM intakes WHERE case_id = ? AND channel = 'text'",
            (case_id,),
        ).fetchone()
    current = row["mx"] if row and row["mx"] is not None else 0
    return current + 1


def next_ocr_line_start(case_id: str, db_path: str = DB_PATH) -> int:
    """Return the next OCR_LINE index for this case (cumulative)."""
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) as n FROM intakes WHERE case_id = ? AND channel = 'ocr'",
            (case_id,),
        ).fetchone()
    count = row["n"] if row else 0
    return (count * 100) + 1  # generous offset so refs never collide


# ---------------------------------------------------------------------------
# AI output helpers
# ---------------------------------------------------------------------------

def save_ai_output(
    case_id: str,
    output_json: str,
    suggested_urgency: str,
    latency_ms: float,
    tokens_in: int,
    tokens_out: int,
    model: str,
    cached: bool,
    db_path: str = DB_PATH,
) -> int:
    """Save a new AI output version. Returns the row id."""
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT MAX(version) as mv FROM ai_outputs WHERE case_id = ?",
            (case_id,),
        ).fetchone()
        version = (row["mv"] or 0) + 1
        cur = conn.execute(
            """INSERT INTO ai_outputs
               (case_id, version, output_json, suggested_urgency, latency_ms,
                tokens_in, tokens_out, model, cached)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                case_id, version, output_json, suggested_urgency,
                latency_ms, tokens_in, tokens_out, model, 1 if cached else 0,
            ),
        )
        return cur.lastrowid


def get_latest_ai_output(case_id: str, db_path: str = DB_PATH) -> Optional[dict]:
    """Return the latest AI output for a case."""
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM ai_outputs WHERE case_id = ? ORDER BY version DESC LIMIT 1",
            (case_id,),
        ).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Clinician action helpers
# ---------------------------------------------------------------------------

def record_clinician_action(
    case_id: str,
    actor_id: str,
    action: str,
    new_urgency: Optional[str] = None,
    reason: Optional[str] = None,
    edited_note: Optional[str] = None,
    db_path: str = DB_PATH,
) -> None:
    """
    Record a clinician action. Enforces override reason length.
    Raises PermissionDenied if actor is not a doctor.
    Raises ValueError if override reason is too short.
    """
    from config import OVERRIDE_REASON_MIN_LEN
    # Require doctor role for approve/override actions
    if action in ("approve", "edit_approve", "override"):
        require_role(actor_id, {"doctor"}, db_path)
    # Enforce override reason in backend (not just UI)
    if action == "override":
        if not reason or len(reason.strip()) < OVERRIDE_REASON_MIN_LEN:
            raise ValueError(
                f"Override reason must be at least {OVERRIDE_REASON_MIN_LEN} characters."
            )
        if not new_urgency:
            raise ValueError("Override requires a new_urgency value.")
    with get_conn(db_path) as conn:
        conn.execute(
            """INSERT INTO clinician_actions
               (case_id, actor, action, new_urgency, reason, edited_note)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (case_id, actor_id, action, new_urgency, reason, edited_note),
        )
    # Set final urgency
    if action in ("approve", "edit_approve"):
        # Get AI-suggested urgency as final
        ai = get_latest_ai_output(case_id, db_path)
        urgency = ai["suggested_urgency"] if ai else "UNCERTAIN_NEEDS_FOLLOWUP"
        set_final_urgency(case_id, urgency, db_path)
    elif action == "override":
        set_final_urgency(case_id, new_urgency, db_path)


def get_clinician_actions(case_id: str, db_path: str = DB_PATH) -> list[dict]:
    with get_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM clinician_actions WHERE case_id = ? ORDER BY created_at",
            (case_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Audit log helpers (low-level — audit.py wraps these)
# ---------------------------------------------------------------------------

def append_audit_row(
    actor_role: str,
    action_type: str,
    case_hash: Optional[str],
    details_json: str,
    confidence: Optional[float],
    prev_hash: str,
    row_hash: str,
    ts: Optional[str] = None,
    db_path: str = DB_PATH,
) -> int:
    """Append one row to audit_logs. Returns the seq."""
    import datetime
    if ts is None:
        ts = datetime.datetime.utcnow().isoformat() + "Z"
    with get_conn(db_path) as conn:
        cur = conn.execute(
            """INSERT INTO audit_logs
               (ts, actor_role, action_type, case_hash, details_json, confidence, prev_hash, hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (ts, actor_role, action_type, case_hash, details_json, confidence, prev_hash, row_hash),
        )
        return cur.lastrowid


def get_all_audit_rows(db_path: str = DB_PATH) -> list[dict]:
    """Return all audit rows ordered by seq."""
    with get_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM audit_logs ORDER BY seq", ()
        ).fetchall()
    return [dict(r) for r in rows]


def get_audit_rows_for_case(case_hash: str, db_path: str = DB_PATH) -> list[dict]:
    """Return audit rows for a specific case hash."""
    with get_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM audit_logs WHERE case_hash = ? ORDER BY seq",
            (case_hash,),
        ).fetchall()
    return [dict(r) for r in rows]
