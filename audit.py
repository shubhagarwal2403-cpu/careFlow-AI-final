"""
audit.py — CareFlow AI append-only hash-chained audit log
No PHI anywhere in audit records. Details validated against an allow-list.
"""

import hashlib
import hmac
import json
import re
from typing import Optional

from config import AUDIT_SALT, AUDIT_DETAIL_MAX_STR_LEN, AUDIT_ALLOWED_DETAIL_KEYS
import db as _db


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _hmac_sha256(secret: str, data: str) -> str:
    """HMAC-SHA256 hex digest."""
    return hmac.new(
        secret.encode("utf-8"),
        data.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _sha256(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _canonical_json(obj: dict) -> str:
    """Deterministic JSON serialisation."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _case_hash(case_id: Optional[str]) -> Optional[str]:
    """Pseudonymise a case_id for the audit log (no PHI)."""
    if case_id is None:
        return None
    return _hmac_sha256(AUDIT_SALT, case_id)


def _get_last_hash(db_path: str) -> str:
    """Return the hash of the last audit row, or the genesis value."""
    rows = _db.get_all_audit_rows(db_path)
    if not rows:
        return "0" * 64
    return rows[-1]["hash"]


# ---------------------------------------------------------------------------
# Details validation (no PHI)
# ---------------------------------------------------------------------------

def _validate_details(details: dict) -> None:
    """
    Ensure details dict only contains allowed keys and safe values.
    Raises ValueError if anything suspicious is found.
    """
    for key, val in details.items():
        if key not in AUDIT_ALLOWED_DETAIL_KEYS:
            raise ValueError(
                f"Audit detail key '{key}' not in allow-list. "
                "Patient text must never be logged."
            )
        if isinstance(val, str) and len(val) > AUDIT_DETAIL_MAX_STR_LEN:
            raise ValueError(
                f"Audit detail value for '{key}' is {len(val)} chars "
                f"(max {AUDIT_DETAIL_MAX_STR_LEN}). Possible PHI leak."
            )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def log_action(
    actor_role: str,
    action_type: str,
    case_id: Optional[str] = None,
    details: Optional[dict] = None,
    confidence: Optional[float] = None,
    db_path: str = _db.DB_PATH,
) -> int:
    """
    Append an entry to the audit log.

    Args:
        actor_role: Role of the user performing the action.
        action_type: One of the defined action type strings.
        case_id: The internal case ID (will be pseudonymised to case_hash).
        details: Dict of safe, non-PHI metadata (validated against allow-list).
        confidence: Optional float confidence score.
        db_path: Database path.

    Returns:
        The seq (row id) of the new audit entry.
    """
    if details is None:
        details = {}
    _validate_details(details)

    case_hash = _case_hash(case_id)
    prev_hash = _get_last_hash(db_path)

    # Build the payload for hashing
    import datetime
    ts = datetime.datetime.utcnow().isoformat() + "Z"
    # We get the next seq by checking current count + 1 (approx; real seq from DB)
    rows = _db.get_all_audit_rows(db_path)
    seq = len(rows) + 1

    payload = _canonical_json({
        "seq": seq,
        "ts": ts,
        "actor_role": actor_role,
        "action_type": action_type,
        "case_hash": case_hash,
        "details": details,
        "confidence": confidence,
    })
    row_hash = _sha256(prev_hash + payload)

    return _db.append_audit_row(
        actor_role=actor_role,
        action_type=action_type,
        case_hash=case_hash,
        details_json=_canonical_json(details),
        confidence=confidence,
        prev_hash=prev_hash,
        row_hash=row_hash,
        ts=ts,
        db_path=db_path,
    )


def verify(db_path: str = _db.DB_PATH) -> dict:
    """
    Verify the entire audit chain.

    Returns:
        {ok: bool, checked: int, first_bad_seq: int|None}
    """
    rows = _db.get_all_audit_rows(db_path)
    prev_hash = "0" * 64

    for row in rows:
        # Recompute expected hash
        payload = _canonical_json({
            "seq": row["seq"],
            "ts": row["ts"],
            "actor_role": row["actor_role"],
            "action_type": row["action_type"],
            "case_hash": row["case_hash"],
            "details": json.loads(row["details_json"]),
            "confidence": row["confidence"],
        })
        expected = _sha256(prev_hash + payload)
        if expected != row["hash"] or row["prev_hash"] != prev_hash:
            return {
                "ok": False,
                "checked": row["seq"],
                "first_bad_seq": row["seq"],
            }
        prev_hash = row["hash"]

    return {
        "ok": True,
        "checked": len(rows),
        "first_bad_seq": None,
    }
