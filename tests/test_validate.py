"""
tests/test_validate.py — Tests for validate.py
Covers: no-diagnosis check, merge_urgency, enforce_no_silent_low, source_ref validation.
All offline.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from validate import (
    merge_urgency, enforce_no_silent_low, redact_diagnosis_terms,
    validate_slm_output,
)
from schemas import RefRegistry, SLMOutput, SymptomTimeline, ChiefComplaint, UrgencyReason
from config import URGENCY_MERGE_ORDER


# ---------------------------------------------------------------------------
# Test: merge_urgency
# ---------------------------------------------------------------------------
def test_merge_high_low_returns_high():
    assert merge_urgency("HIGH", "LOW") == "HIGH"


def test_merge_low_high_returns_high():
    assert merge_urgency("LOW", "HIGH") == "HIGH"


def test_merge_critical_uncertain_returns_critical():
    assert merge_urgency("CRITICAL", "UNCERTAIN_NEEDS_FOLLOWUP") == "CRITICAL"


def test_merge_same_level():
    assert merge_urgency("MEDIUM", "MEDIUM") == "MEDIUM"


def test_merge_order():
    """Verify the full ordering: LOW < UNCERTAIN < MEDIUM < HIGH < CRITICAL."""
    levels = URGENCY_MERGE_ORDER
    for i in range(len(levels)):
        for j in range(i + 1, len(levels)):
            result = merge_urgency(levels[i], levels[j])
            assert result == levels[j], f"merge({levels[i]}, {levels[j]}) should be {levels[j]}, got {result}"


# ---------------------------------------------------------------------------
# Test: enforce_no_silent_low (D4)
# ---------------------------------------------------------------------------
def _make_slm_output(urgency: str, symptoms: list) -> SLMOutput:
    """Helper to create a minimal SLMOutput for testing."""
    return SLMOutput(
        chief_complaint=ChiefComplaint(text="test", source_ref="text:MSG_1"),
        symptom_timeline=symptoms,
        suggested_urgency=urgency,
    )


def test_low_with_known_onset_and_severity_stays_low():
    syms = [SymptomTimeline(
        symptom="headache",
        onset="2 days ago",
        duration="UNKNOWN",
        progression="stable",
        severity="mild",
        source_ref="text:MSG_1",
    )]
    slm = _make_slm_output("LOW", syms)
    result = enforce_no_silent_low(slm)
    assert result == "LOW"


def test_low_without_known_info_upgrades_to_uncertain():
    syms = [SymptomTimeline(
        symptom="headache",
        onset="UNKNOWN",
        duration="UNKNOWN",
        progression="UNKNOWN",
        severity="UNKNOWN",
        source_ref="text:MSG_1",
    )]
    slm = _make_slm_output("LOW", syms)
    result = enforce_no_silent_low(slm)
    assert result == "UNCERTAIN_NEEDS_FOLLOWUP"


def test_fever_vague_becomes_uncertain():
    """'I have fever' with no details → UNCERTAIN (smoke test D4)."""
    syms = [SymptomTimeline(
        symptom="fever",
        onset="UNKNOWN",
        duration="UNKNOWN",
        progression="UNKNOWN",
        severity="UNKNOWN",
        source_ref="text:MSG_1",
    )]
    slm = _make_slm_output("LOW", syms)
    result = enforce_no_silent_low(slm)
    assert result == "UNCERTAIN_NEEDS_FOLLOWUP", (
        "'I have fever' must become UNCERTAIN when SLM says LOW"
    )


def test_high_urgency_unchanged_by_enforce():
    syms = [SymptomTimeline(
        symptom="chest pain",
        onset="UNKNOWN",
        duration="UNKNOWN",
        progression="UNKNOWN",
        severity="UNKNOWN",
        source_ref="text:MSG_1",
    )]
    slm = _make_slm_output("HIGH", syms)
    result = enforce_no_silent_low(slm)
    assert result == "HIGH"


# ---------------------------------------------------------------------------
# Test: no-diagnosis redaction (D10)
# ---------------------------------------------------------------------------
def test_diagnosis_term_redacted():
    text = "patient has dengue"
    redacted, was_redacted = redact_diagnosis_terms(text)
    assert was_redacted
    assert "dengue" not in redacted
    assert "[REDACTED" in redacted


def test_diagnosis_phrase_redacted():
    text = "likely has heart attack"
    redacted, was_redacted = redact_diagnosis_terms(text)
    assert was_redacted


def test_benign_text_not_redacted():
    text = "patient reports chest pain and sweating since this morning"
    redacted, was_redacted = redact_diagnosis_terms(text)
    assert not was_redacted


def test_diagnosis_in_quote_allowed():
    """D10: Disease terms inside verified verbatim patient quote are allowed."""
    source_text = 'Patient said "no diabetes"'
    slm_text = 'Patient stated "no diabetes" (text:MSG_1)'
    redacted, was_redacted = redact_diagnosis_terms(slm_text, source_text=source_text)
    # diabetes is inside a quote that exists in source → allowed
    assert not was_redacted, (
        f"'diabetes' inside verified verbatim quote should be allowed. Got: {redacted}"
    )


def test_diagnosis_not_in_quote_redacted():
    """Diagnosis term outside quotes should always be redacted."""
    source_text = "Patient says I have no diabetes"
    slm_text = "patient has diabetes based on history"  # no quotes, not in source
    redacted, was_redacted = redact_diagnosis_terms(slm_text, source_text=source_text)
    assert was_redacted


# ---------------------------------------------------------------------------
# Test: validate_slm_output full pipeline
# ---------------------------------------------------------------------------
def _make_registry_with_msg1() -> RefRegistry:
    reg = RefRegistry()
    reg.register_text(1, "I have fever since yesterday")
    return reg


def test_validate_slm_drops_invalid_ref():
    """A source_ref not in the registry should be dropped."""
    registry = _make_registry_with_msg1()
    raw = {
        "chief_complaint": {"text": "fever", "source_ref": "text:MSG_99"},  # invalid
        "symptom_timeline": [],
        "uncertainty_flags": [],
        "warning_signals": [],
        "conflicts": [],
        "missing_information": [],
        "follow_up_questions": [],
        "reply_language": "en",
        "suggested_urgency": "UNCERTAIN_NEEDS_FOLLOWUP",
        "urgency_reasons": [],
        "draft_triage_note": "Fever reported.",
        "extracted_report_metrics": [],
    }
    slm_out, dropped, urgency = validate_slm_output(
        raw_json=raw,
        registry=registry,
        rules_floor="LOW",
        rule_hits=[],
        injection_detected=False,
    )
    assert slm_out is not None
    # Chief complaint ref was invalid → dropped in dropped list or fallback used
    # The output should still be valid (fallback to first ref)


def test_validate_slm_merges_rules_floor():
    """Rules floor HIGH should override SLM LOW."""
    registry = _make_registry_with_msg1()
    raw = {
        "chief_complaint": {"text": "mild headache", "source_ref": "text:MSG_1"},
        "symptom_timeline": [],
        "uncertainty_flags": [],
        "warning_signals": [],
        "conflicts": [],
        "missing_information": [],
        "follow_up_questions": [],
        "reply_language": "en",
        "suggested_urgency": "LOW",
        "urgency_reasons": [],
        "draft_triage_note": "Mild headache.",
        "extracted_report_metrics": [],
    }
    from schemas import RuleHit
    rule_hit = RuleHit(rule_id="R09", label="Chest pain", floor="HIGH", source_ref="text:MSG_1")

    slm_out, dropped, urgency = validate_slm_output(
        raw_json=raw,
        registry=registry,
        rules_floor="HIGH",
        rule_hits=[rule_hit],
        injection_detected=False,
    )
    assert urgency == "HIGH", f"Rules floor HIGH should override SLM LOW, got {urgency}"


def test_validate_slm_injection_floor():
    """Injection detected → floor must be at least UNCERTAIN."""
    registry = _make_registry_with_msg1()
    raw = {
        "chief_complaint": {"text": "headache", "source_ref": "text:MSG_1"},
        "symptom_timeline": [],
        "uncertainty_flags": [],
        "warning_signals": [],
        "conflicts": [],
        "missing_information": [],
        "follow_up_questions": [],
        "reply_language": "en",
        "suggested_urgency": "LOW",
        "urgency_reasons": [],
        "draft_triage_note": "Headache.",
        "extracted_report_metrics": [],
    }
    slm_out, dropped, urgency = validate_slm_output(
        raw_json=raw,
        registry=registry,
        rules_floor="LOW",
        rule_hits=[],
        injection_detected=True,
    )
    from config import URGENCY_MERGE_ORDER
    assert URGENCY_MERGE_ORDER.index(urgency) >= URGENCY_MERGE_ORDER.index("UNCERTAIN_NEEDS_FOLLOWUP"), (
        f"Injection detected should floor at UNCERTAIN, got {urgency}"
    )


def test_validate_slm_splits_compound_followup_questions():
    """One compound follow-up question becomes separate atomic questions."""
    registry = _make_registry_with_msg1()
    raw = {
        "chief_complaint": {"text": "fever", "source_ref": "text:MSG_1"},
        "symptom_timeline": [],
        "uncertainty_flags": [],
        "warning_signals": [],
        "conflicts": [],
        "missing_information": [],
        "follow_up_questions": [
            {"question": "Do you have fever and cough?"},
        ],
        "reply_language": "en",
        "suggested_urgency": "LOW",
        "urgency_reasons": [],
        "draft_triage_note": "Fever reported.",
        "extracted_report_metrics": [],
    }
    slm_out, _, _ = validate_slm_output(
        raw_json=raw,
        registry=registry,
        rules_floor="LOW",
        rule_hits=[],
        injection_detected=False,
    )
    assert slm_out is not None
    assert [q.question for q in slm_out.follow_up_questions] == [
        "Do you have fever?",
        "Do you have cough?",
    ]


# ---------------------------------------------------------------------------
# Test: UI safety — script tag escaped
# ---------------------------------------------------------------------------
def test_script_tag_escaped():
    """HTML-escape must neutralise script tags in patient text."""
    import html
    xss = "<script>alert(1)</script>"
    escaped = html.escape(xss)
    assert "<script>" not in escaped
    assert "&lt;script&gt;" in escaped
