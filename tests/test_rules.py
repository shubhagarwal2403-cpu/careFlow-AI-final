"""
tests/test_rules.py — Tests for the deterministic rules engine
All offline — no API calls.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from rules import check_rules
from tests.fixtures.phrases import (
    RED_FLAG_MUST_BE_CRITICAL_OR_HIGH,
    BENIGN_MUST_NOT_BE_CRITICAL,
    NEGATED_MUST_NOT_BE_CRITICAL,
)
from config import URGENCY_MERGE_ORDER


# ---------------------------------------------------------------------------
# Test: all red-flag phrases give floor >= HIGH
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("phrase", RED_FLAG_MUST_BE_CRITICAL_OR_HIGH)
def test_red_flag_must_be_critical_or_high(phrase):
    result = check_rules([phrase])
    floor = result["floor"]
    idx = URGENCY_MERGE_ORDER.index(floor)
    # HIGH or CRITICAL
    assert idx >= URGENCY_MERGE_ORDER.index("HIGH"), (
        f"Expected HIGH or CRITICAL for: {phrase!r}\n"
        f"Got: {floor}\nHits: {[h.label for h in result['hits']]}"
    )


# ---------------------------------------------------------------------------
# Test: chest pain + sweating → CRITICAL specifically
# ---------------------------------------------------------------------------
def test_chest_pain_plus_sweating_critical():
    result = check_rules(["chest pain and sweating since 10 mins"])
    assert result["floor"] == "CRITICAL", (
        f"Expected CRITICAL for chest pain + sweating, got {result['floor']}"
    )


def test_cardiac_hinglish_critical():
    result = check_rules(["Sir, kal raat se left chest mein heavy pain ho raha hai aur pasina aa raha hai"])
    assert result["floor"] == "CRITICAL"


# ---------------------------------------------------------------------------
# Test: benign phrases are not CRITICAL
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("phrase", BENIGN_MUST_NOT_BE_CRITICAL)
def test_benign_must_not_be_critical(phrase):
    result = check_rules([phrase])
    assert result["floor"] != "CRITICAL", (
        f"Expected non-CRITICAL for: {phrase!r}\nGot: {result['floor']}"
    )


# ---------------------------------------------------------------------------
# Test: negated red-flag phrases not CRITICAL and carry AMBIGUOUS flag
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("phrase", NEGATED_MUST_NOT_BE_CRITICAL)
def test_negated_must_not_be_critical(phrase):
    result = check_rules([phrase])
    assert result["floor"] != "CRITICAL", (
        f"Expected non-CRITICAL for negated: {phrase!r}\nGot: {result['floor']}"
    )


def test_negated_has_ambiguous_flag():
    result = check_rules(["seene mein dard nahi hai, bas thodi khansi hai"])
    assert result["floor"] != "CRITICAL"
    # Should have a negated hit
    assert len(result["negated"]) > 0, "Expected at least one negated hit"


def test_negated_floor_at_least_uncertain():
    result = check_rules(["no chest pain, only mild cough"])
    floor_idx = URGENCY_MERGE_ORDER.index(result["floor"])
    uncertain_idx = URGENCY_MERGE_ORDER.index("UNCERTAIN_NEEDS_FOLLOWUP")
    assert floor_idx >= uncertain_idx, (
        f"Negated red flag should be at least UNCERTAIN, got {result['floor']}"
    )


# ---------------------------------------------------------------------------
# Test: vitals rules
# ---------------------------------------------------------------------------
def test_vitals_spo2_critical():
    result = check_rules([], vitals={"SPO2": 88})
    assert result["floor"] == "CRITICAL", f"SpO2 88 should be CRITICAL, got {result['floor']}"


def test_vitals_hr_high():
    result = check_rules([], vitals={"HR": 140})
    assert result["floor"] in ("HIGH", "CRITICAL"), f"HR 140 should be HIGH+, got {result['floor']}"


def test_vitals_sbp_high():
    result = check_rules([], vitals={"SBP": 190})
    assert result["floor"] in ("HIGH", "CRITICAL"), f"SBP 190 should be HIGH+, got {result['floor']}"


def test_two_vitals_breach_critical():
    """Two vitals breaches → CRITICAL regardless of individual levels."""
    result = check_rules([], vitals={"SpO2": 91, "HR": 135})
    assert result["floor"] == "CRITICAL", (
        f"Two vitals breaches should be CRITICAL, got {result['floor']}"
    )


def test_vitals_plus_redflag_critical():
    """Any vitals breach + any red-flag symptom → CRITICAL."""
    result = check_rules(
        ["shortness of breath"],
        vitals={"SBP": 190},
    )
    assert result["floor"] == "CRITICAL", (
        f"Vitals breach + breathlessness should be CRITICAL, got {result['floor']}"
    )


# ---------------------------------------------------------------------------
# Test: suicidal
# ---------------------------------------------------------------------------
def test_suicidal_intent_critical():
    result = check_rules(["mujhe jeene ka mann nahi hai, I want to end my life"])
    assert result["floor"] == "CRITICAL"


def test_suicidal_ideation_high():
    result = check_rules(["jeena nahi chahta, zindagi se thak gaya hoon"])
    floor_idx = URGENCY_MERGE_ORDER.index(result["floor"])
    assert floor_idx >= URGENCY_MERGE_ORDER.index("HIGH"), (
        f"Suicidal ideation should be >= HIGH, got {result['floor']}"
    )


# ---------------------------------------------------------------------------
# Test: Devanagari variants
# ---------------------------------------------------------------------------
def test_devanagari_chest_pain():
    result = check_rules(["सीने में दर्द और पसीना आ रहा है"])
    assert result["floor"] in ("HIGH", "CRITICAL")


def test_devanagari_unconscious():
    result = check_rules(["वह बेहोश है"])
    # "बेहोश" should match unconscious pattern
    assert result["floor"] in ("HIGH", "CRITICAL")


# ---------------------------------------------------------------------------
# Test: rule hits structure
# ---------------------------------------------------------------------------
def test_rule_hit_has_required_fields():
    result = check_rules(["chest pain and sweating"])
    assert len(result["hits"]) > 0
    hit = result["hits"][0]
    assert hasattr(hit, "rule_id")
    assert hasattr(hit, "label")
    assert hasattr(hit, "floor")
    assert hasattr(hit, "source_ref")
