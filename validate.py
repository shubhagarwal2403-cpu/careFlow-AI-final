"""
validate.py — CareFlow AI output validation
1. Pydantic schema + enum check
2. source_ref check against RefRegistry (drop invalid items)
3. evidence_quote verbatim check (D7)
4. No-diagnosis check + redaction (D10)
5. enforce_no_silent_low (D4)
6. merge_urgency (rules floor + SLM)
"""

import re
import json
from typing import Optional

from pydantic import ValidationError

from schemas import (
    SLMOutput, PipelineResult, UncertaintyFlag, UrgencyLevel,
    RefRegistry, RuleHit,
)
from config import URGENCY_MERGE_ORDER, DIAGNOSIS_DENYLIST
from followup import split_followup_questions
import audit


# ---------------------------------------------------------------------------
# Urgency merge
# ---------------------------------------------------------------------------

def merge_urgency(a: str, b: str) -> str:
    """
    Return the higher urgency level of two inputs.
    Uses URGENCY_MERGE_ORDER: LOW < UNCERTAIN < MEDIUM < HIGH < CRITICAL.
    """
    idx_a = URGENCY_MERGE_ORDER.index(a)
    idx_b = URGENCY_MERGE_ORDER.index(b)
    return a if idx_a >= idx_b else b


# ---------------------------------------------------------------------------
# Diagnosis deny-list check (D10)
# ---------------------------------------------------------------------------

_DIAG_PATTERNS = [re.compile(p, re.IGNORECASE | re.UNICODE) for p in DIAGNOSIS_DENYLIST]
_REDACT_MARKER = "[REDACTED: diagnosis-like]"


def _contains_diagnosis(text: str) -> bool:
    """True if the text contains any diagnosis-like term."""
    for pat in _DIAG_PATTERNS:
        if pat.search(text):
            return True
    return False


def _is_inside_verbatim_quote(text: str, match: re.Match, source_text: str) -> bool:
    """
    D10: Disease terms are allowed only inside a verbatim quote of patient/report text.
    Check if the matched span appears inside quotes AND that quoted text exists in source.
    """
    full_text = text
    start = match.start()
    end = match.end()

    # Look for enclosing quotes
    for quote_pat in [r'"([^"]{1,200})"', r"'([^']{1,200})'", r"'([^']{1,200})'"]:
        for qm in re.finditer(quote_pat, full_text, re.UNICODE):
            if qm.start() <= start and qm.end() >= end:
                # The match is inside a quote — verify the quoted text is in the source
                quoted_content = qm.group(1)
                normalised_quote = " ".join(quoted_content.split())
                normalised_source = " ".join(source_text.split())
                if normalised_quote in normalised_source:
                    return True
    return False


def redact_diagnosis_terms(
    text: str,
    source_text: str = "",
    case_id: Optional[str] = None,
    db_path: Optional[str] = None,
) -> tuple[str, bool]:
    """
    Redact diagnosis-like terms from text.
    Returns (redacted_text, was_redacted).
    """
    was_redacted = False
    result = text

    for pat in _DIAG_PATTERNS:
        # pat is already compiled with IGNORECASE | UNICODE
        for m in pat.finditer(result):
            if not _is_inside_verbatim_quote(result, m, source_text):
                result = result[: m.start()] + _REDACT_MARKER + result[m.end():]
                was_redacted = True
                if case_id and db_path:
                    try:
                        audit.log_action(
                            actor_role="system",
                            action_type="validator_redaction",
                            case_id=case_id,
                            details={"validator_flag": "diagnosis_term"},
                            db_path=db_path,
                        )
                    except Exception:
                        pass  # Don't block on audit failures
                break  # Restart loop after edit
    return result, was_redacted


def _redact_all_strings(obj, source_text: str = "", case_id=None, db_path=None):
    """Recursively redact diagnosis terms from all string fields of a dict/list."""
    if isinstance(obj, str):
        redacted, _ = redact_diagnosis_terms(obj, source_text, case_id, db_path)
        return redacted
    elif isinstance(obj, dict):
        return {k: _redact_all_strings(v, source_text, case_id, db_path) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_redact_all_strings(item, source_text, case_id, db_path) for item in obj]
    return obj


# ---------------------------------------------------------------------------
# enforce_no_silent_low (D4)
# ---------------------------------------------------------------------------

def enforce_no_silent_low(slm_output: SLMOutput) -> str:
    """
    D4: LOW urgency is only allowed if at least one symptom has known
    onset/duration AND known severity. Otherwise upgrade to UNCERTAIN.
    """
    if slm_output.suggested_urgency != "LOW":
        return slm_output.suggested_urgency

    for symptom in slm_output.symptom_timeline:
        has_temporal = (
            symptom.onset not in ("UNKNOWN", "", None)
            or symptom.duration not in ("UNKNOWN", "", None)
        )
        has_severity = symptom.severity not in ("UNKNOWN", "", None)
        if has_temporal and has_severity:
            return "LOW"  # OK to stay LOW

    return "UNCERTAIN_NEEDS_FOLLOWUP"  # upgrade


# ---------------------------------------------------------------------------
# source_ref validation
# ---------------------------------------------------------------------------

def _validate_source_refs(slm_dict: dict, registry: RefRegistry) -> tuple[dict, list[str]]:
    """
    Walk all source_ref fields in the SLM output dict.
    Drop items with invalid refs. Return (cleaned_dict, dropped_refs).
    """
    dropped = []

    def _clean_list(lst: list, ref_field: str = "source_ref") -> list:
        result = []
        for item in lst:
            if isinstance(item, dict) and ref_field in item:
                ref = item[ref_field]
                if not registry.is_valid(ref):
                    dropped.append(ref)
                    continue
            result.append(item)
        return result

    cleaned = dict(slm_dict)

    # Validate refs in list fields
    for list_field in [
        "symptom_timeline", "extracted_report_metrics",
        "uncertainty_flags", "warning_signals",
        "urgency_reasons",
    ]:
        if list_field in cleaned:
            cleaned[list_field] = _clean_list(cleaned[list_field])

    # Chief complaint
    if "chief_complaint" in cleaned:
        ref = cleaned["chief_complaint"].get("source_ref", "")
        if not registry.is_valid(ref):
            dropped.append(ref)
            # Use first registered ref as fallback
            all_refs = registry.all_refs()
            cleaned["chief_complaint"]["source_ref"] = all_refs[0] if all_refs else "text:MSG_1"

    # Validate evidence_quote in symptom_timeline (D7)
    for item in cleaned.get("symptom_timeline", []):
        if "evidence_quote" in item and item["evidence_quote"]:
            # Check if evidence_quote exists in the source
            ref = item.get("source_ref", "")
            resolved = registry.resolve(ref)
            if resolved:
                norm_source = " ".join(resolved.text.split())
                norm_quote = " ".join(item["evidence_quote"].split())
                if norm_quote not in norm_source:
                    item["evidence_quote"] = None  # Drop invalid quote, keep the claim

    return cleaned, dropped


# ---------------------------------------------------------------------------
# Main validation function
# ---------------------------------------------------------------------------

def validate_slm_output(
    raw_json: dict,
    registry: RefRegistry,
    rules_floor: str,
    rule_hits: list[RuleHit],
    injection_detected: bool,
    case_id: Optional[str] = None,
    db_path: Optional[str] = None,
    source_text: str = "",  # full concatenation of all patient text for quote verification
) -> tuple[Optional[SLMOutput], list[str], str]:
    """
    Validate and clean SLM output.

    Args:
        raw_json: Parsed JSON dict from the SLM.
        registry: The case's RefRegistry.
        rules_floor: Urgency floor from the rules engine.
        rule_hits: Rule hits to include as warning signals.
        injection_detected: Whether injection was detected (adds security floor).
        case_id: For audit logging.
        db_path: For audit logging.
        source_text: Concatenated original patient text for quote verification.

    Returns:
        (SLMOutput | None, dropped_refs, final_urgency)
        Returns None if the output is fundamentally malformed.
    """
    # 1. Validate and clean source refs
    cleaned, dropped_refs = _validate_source_refs(raw_json, registry)

    # 1b. Ask one thing per follow-up question, even if the model combined
    # several symptoms into one item.
    if isinstance(cleaned.get("follow_up_questions"), list):
        cleaned["follow_up_questions"] = split_followup_questions(
            cleaned["follow_up_questions"]
        )

    # 2. Redact diagnosis terms from all string fields
    all_refs = registry.all_refs()
    cleaned = _redact_all_strings(cleaned, source_text, case_id, db_path)

    # 3. Parse with Pydantic
    try:
        slm_output = SLMOutput(**cleaned)
    except (ValidationError, TypeError) as e:
        return None, dropped_refs, rules_floor  # signal caller to retry

    # 4. Enforce no-silent-low (D4)
    adjusted_urgency = enforce_no_silent_low(slm_output)
    if adjusted_urgency != slm_output.suggested_urgency:
        slm_output.suggested_urgency = adjusted_urgency  # type: ignore[assignment]

    # 5. Add negation AMBIGUOUS flags for negated rule hits
    for rh in rule_hits:
        if rh.negated:
            slm_output.uncertainty_flags.append(UncertaintyFlag(
                issue_type="AMBIGUOUS",
                reason=f"Red-flag symptom mentioned with negation — clinician to verify ({rh.label})",
                source_ref=rh.source_ref,
            ))

    # 6. Add rule hits as warning signals (symptom-only labels)
    for rh in rule_hits:
        if not rh.negated:
            from schemas import WarningSignal
            slm_output.warning_signals.append(WarningSignal(
                signal=f"Rule fired [{rh.rule_id}]: {rh.label}",
                source_ref=rh.source_ref,
            ))

    # 7. Merge urgency: max(rules_floor, slm_suggestion)
    merged = merge_urgency(rules_floor, slm_output.suggested_urgency)

    # 8. Apply injection security floor (D6)
    if injection_detected:
        merged = merge_urgency(merged, "UNCERTAIN_NEEDS_FOLLOWUP")

    return slm_output, dropped_refs, merged
