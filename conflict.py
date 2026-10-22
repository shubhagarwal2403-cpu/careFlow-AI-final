"""
conflict.py — CareFlow AI conflict detection
Hybrid: deterministic (CONFLICT_MAP) + SLM-found conflicts.
Detects contradictions between patient statements and uploaded report data.
Alert text states the mismatch only — NO diagnosis terms.
"""

import re
from typing import Optional

from config import CONFLICT_MAP
from schemas import ConflictAlert, RefRegistry


def detect_conflicts(
    text_msgs: list[tuple[str, str]],        # (text, source_ref) patient text messages
    ocr_lines: list[tuple[str, str]],         # (line_text, source_ref) from OCR
    slm_conflicts: Optional[list[dict]] = None,  # conflicts found by the SLM
    registry: Optional[RefRegistry] = None,
) -> list[ConflictAlert]:
    """
    Detect conflicts between patient statements and report findings.

    Implements D11:
    (a) Deterministic checks using CONFLICT_MAP
    (b) SLM-found conflicts (validated and deduped)

    Returns a list of ConflictAlert objects.
    """
    alerts: list[ConflictAlert] = []

    # --- (a) Deterministic conflict detection ---
    for patient_text, patient_ref in text_msgs:
        patient_norm = patient_text.lower()

        for rule in CONFLICT_MAP:
            denial_pat = re.compile(rule["denial_pattern"], re.IGNORECASE | re.UNICODE)
            if not denial_pat.search(patient_norm):
                continue

            # Patient denied something — check if report contradicts it
            for line_text, line_ref in ocr_lines:
                line_norm = line_text.lower()
                for indicator_pat in rule["report_indicators"]:
                    if re.search(indicator_pat, line_norm, re.IGNORECASE | re.UNICODE):
                        # Build summary with verbatim quotes (D10 compliant)
                        # Extract the denial phrase for quoting
                        denial_match = denial_pat.search(patient_norm)
                        denial_quote = denial_match.group(0) if denial_match else "stated denial"

                        summary = (
                            f"Patient stated '{denial_quote}' ({patient_ref}) "
                            f"but the report contains a related finding ({line_ref}): "
                            f"'{line_text.strip()[:80]}'"
                        )

                        alert = ConflictAlert(
                            summary=summary,
                            source_a=patient_ref,
                            source_b=line_ref,
                        )
                        # Avoid duplicates
                        if not _is_duplicate(alert, alerts):
                            alerts.append(alert)
                        break

    # --- (b) SLM-found conflicts ---
    if slm_conflicts:
        for c in slm_conflicts:
            source_a = c.get("source_a", "")
            source_b = c.get("source_b", "")
            summary = c.get("summary", "")

            # Validate refs if registry is provided
            if registry:
                if not registry.is_valid(source_a) or not registry.is_valid(source_b):
                    continue  # drop conflicts with invalid refs

            if not summary or not source_a or not source_b:
                continue

            alert = ConflictAlert(
                summary=summary,
                source_a=source_a,
                source_b=source_b,
            )
            if not _is_duplicate(alert, alerts):
                alerts.append(alert)

    return alerts


def _is_duplicate(alert: ConflictAlert, existing: list[ConflictAlert]) -> bool:
    """Check if a conflict alert is already in the list (by source pair)."""
    for e in existing:
        if (
            (e.source_a == alert.source_a and e.source_b == alert.source_b)
            or (e.source_a == alert.source_b and e.source_b == alert.source_a)
        ):
            return True
    return False
