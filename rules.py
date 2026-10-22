"""
rules.py — CareFlow AI deterministic red-flag triage floor
Processes masked text + vitals → urgency floor + rule hits + negation flags.
This runs BEFORE the SLM and its output is a floor that the SLM cannot lower.

All rule labels are symptom-only. No disease names or diagnoses.
"""

import re
import unicodedata
from typing import Optional

from config import (
    RULES_TABLE,
    RULES_PATTERNS,
    VITALS_RULES,
    URGENCY_MERGE_ORDER,
    NEGATION_WINDOW,
    NEGATION_WORDS,
)
from schemas import RuleHit


# ---------------------------------------------------------------------------
# Text normalisation
# ---------------------------------------------------------------------------

def normalise(text: str) -> str:
    """
    Lowercase, NFKC unicode, collapse whitespace.
    Applied before pattern matching to handle variant spellings.
    """
    text = unicodedata.normalize("NFKC", text)
    text = text.lower()
    text = re.sub(r"\s+", " ", text)
    text = text.strip()
    return text


# ---------------------------------------------------------------------------
# Negation detection (D5)
# ---------------------------------------------------------------------------

_NEGATION_RE = re.compile(
    "|".join(NEGATION_WORDS),
    flags=re.IGNORECASE | re.UNICODE,
)


def _is_negated(text: str, match_start: int, match_end: int) -> bool:
    """
    Return True if the match is within NEGATION_WINDOW tokens of a negation word.
    Works on the raw (normalised) text string using token splitting.
    Excludes phrases where negation words are inherently part of the symptom
    (e.g., 'not responding', 'not able to speak', 'bol nahi pa raha').
    """
    matched_substring = text[match_start:match_end].lower()
    # If the matched symptom pattern itself already contains 'not', 'nahi', etc., do not treat it as negated
    for neg in [r"\bnot\b", r"\bnahi\b", r"\bnhi\b", r"\bनहीं\b"]:
        if re.search(neg, matched_substring):
            return False

    tokens = text.split()
    # Find which token indices correspond to the match
    char_pos = 0
    match_token_indices = []
    for i, tok in enumerate(tokens):
        tok_start = char_pos
        tok_end = char_pos + len(tok)
        # Check overlap with match
        if tok_start < match_end and tok_end > match_start:
            match_token_indices.append(i)
        char_pos = tok_end + 1  # +1 for space

    if not match_token_indices:
        return False

    centre = match_token_indices[0]
    window_start = max(0, centre - NEGATION_WINDOW)
    window_end = min(len(tokens), centre + NEGATION_WINDOW + 1)
    
    # Exclude tokens belonging to the match itself when checking for negation
    window_tokens_list = [
        tok for idx, tok in enumerate(tokens[window_start:window_end], start=window_start)
        if idx not in match_token_indices
    ]
    window_tokens = " ".join(window_tokens_list)

    return bool(_NEGATION_RE.search(window_tokens))


# ---------------------------------------------------------------------------
# Urgency ordering helper
# ---------------------------------------------------------------------------

def _merge(a: str, b: str) -> str:
    """Return the higher urgency of two levels."""
    return a if URGENCY_MERGE_ORDER.index(a) >= URGENCY_MERGE_ORDER.index(b) else b


# ---------------------------------------------------------------------------
# Core rule engine
# ---------------------------------------------------------------------------

def check_rules(
    texts: list[str],        # list of masked text strings (one per intake)
    vitals: Optional[dict] = None,  # {"SPO2": 88, "HR": 120, "SBP": 190, "TEMP": 38.5}
    source_refs: Optional[list[str]] = None,  # parallel to texts
) -> dict:
    """
    Run all deterministic red-flag rules on the provided texts and vitals.

    Returns:
        {
            "floor": str (urgency level),
            "hits": [RuleHit],     # rules that fired (non-negated)
            "negated": [RuleHit],  # rules matched but negated
        }
    """
    if source_refs is None:
        source_refs = [f"text:MSG_{i+1}" for i in range(len(texts))]

    hits: list[RuleHit] = []
    negated_hits: list[RuleHit] = []
    floor = "LOW"

    # Combine all texts for cross-message pattern matching
    combined_texts = [(normalise(t), ref) for t, ref in zip(texts, source_refs)]

    # --- Check each rule ---
    for rule in RULES_TABLE:
        rule_id = rule["rule_id"]
        label = rule["label"]
        rule_floor = rule["floor"]
        patterns_key = rule["patterns_key"]

        if patterns_key == "chest_pain_plus":
            # Special compound rule: chest pain + any associated critical symptom
            _check_chest_pain_plus(
                combined_texts, rule_id, label, hits, negated_hits
            )
            # If R08 hit was added, raise floor to CRITICAL immediately
            if any(h.rule_id == "R08" for h in hits):
                floor = _merge(floor, "CRITICAL")
            continue

        patterns = RULES_PATTERNS.get(patterns_key, [])
        matched = False
        matched_ref = source_refs[0] if source_refs else "text:MSG_1"

        for norm_text, ref in combined_texts:
            for pat in patterns:
                for m in re.finditer(pat, norm_text, re.IGNORECASE | re.UNICODE):
                    if _is_negated(norm_text, m.start(), m.end()):
                        # Negated — add AMBIGUOUS flag but don't raise floor beyond UNCERTAIN
                        negated_hits.append(RuleHit(
                            rule_id=rule_id,
                            label=label,
                            floor="UNCERTAIN_NEEDS_FOLLOWUP",
                            source_ref=ref,
                            negated=True,
                        ))
                    else:
                        matched = True
                        matched_ref = ref
                        break
                if matched:
                    break
            if matched:
                break

        if matched:
            hit = RuleHit(
                rule_id=rule_id,
                label=label,
                floor=rule_floor,
                source_ref=matched_ref,
                negated=False,
            )
            hits.append(hit)
            floor = _merge(floor, rule_floor)

    # --- Vitals rules ---
    vitals_hits = []
    if vitals:
        vitals_hits, vitals_floor = _check_vitals(vitals)
        hits.extend(vitals_hits)
        floor = _merge(floor, vitals_floor)

    # Any vitals breach + any red-flag symptom → CRITICAL (from spec §8)
    if vitals_hits and len(hits) > len(vitals_hits):
        floor = _merge(floor, "CRITICAL")

    # Negated hits contribute UNCERTAIN floor
    for nh in negated_hits:
        floor = _merge(floor, "UNCERTAIN_NEEDS_FOLLOWUP")

    return {
        "floor": floor,
        "hits": hits,
        "negated": negated_hits,
    }


def _check_chest_pain_plus(
    combined_texts: list[tuple[str, str]],
    rule_id: str,
    label: str,
    hits: list[RuleHit],
    negated_hits: list[RuleHit],
) -> None:
    """
    R08: CRITICAL if chest pain AND any associated critical symptom
         (sweating, breathlessness, nausea, left arm/jaw pain, radiation).
    If chest pain alone (no associated), R09 fires with HIGH floor (handled separately).
    """
    chest_patterns = RULES_PATTERNS["chest_pain"]
    assoc_patterns = RULES_PATTERNS["associated_critical"]

    chest_found = False
    assoc_found = False
    chest_ref = "text:MSG_1"
    assoc_ref = "text:MSG_1"
    chest_negated = False

    for norm_text, ref in combined_texts:
        for pat in chest_patterns:
            for m in re.finditer(pat, norm_text, re.IGNORECASE | re.UNICODE):
                if _is_negated(norm_text, m.start(), m.end()):
                    chest_negated = True
                else:
                    chest_found = True
                    chest_ref = ref
                    break
            if chest_found:
                break

        for pat in assoc_patterns:
            if re.search(pat, norm_text, re.IGNORECASE | re.UNICODE):
                assoc_found = True
                assoc_ref = ref
                break

    if chest_found and assoc_found:
        hits.append(RuleHit(
            rule_id=rule_id,
            label=label,
            floor="CRITICAL",
            source_ref=chest_ref,
            negated=False,
        ))
    elif chest_negated:
        negated_hits.append(RuleHit(
            rule_id=rule_id,
            label="Chest pain mentioned with negation — clinician to verify",
            floor="UNCERTAIN_NEEDS_FOLLOWUP",
            source_ref=chest_ref,
            negated=True,
        ))


def _check_vitals(vitals: dict) -> tuple[list[RuleHit], str]:
    """Check vitals against thresholds. Returns (hits, floor)."""
    hits: list[RuleHit] = []
    floor = "LOW"
    breach_count = 0

    # Normalise keys (case-insensitive: SPO2, SpO2, spo2)
    vitals_norm = {str(k).upper(): v for k, v in vitals.items()}
    spo2 = vitals_norm.get("SPO2")
    hr = vitals_norm.get("HR")
    sbp = vitals_norm.get("SBP")
    temp = vitals_norm.get("TEMP")

    vr = VITALS_RULES

    # SpO2
    if spo2 is not None:
        if spo2 < vr["SPO2"]["CRITICAL_thresh"]:
            hits.append(RuleHit(
                rule_id="VR01", label=f"SpO2 critically low ({spo2}%)",
                floor="CRITICAL", source_ref="vitals:SPO2",
            ))
            floor = _merge(floor, "CRITICAL")
            breach_count += 1
        elif spo2 < vr["SPO2"]["HIGH_thresh"]:
            hits.append(RuleHit(
                rule_id="VR01", label=f"SpO2 low ({spo2}%)",
                floor="HIGH", source_ref="vitals:SPO2",
            ))
            floor = _merge(floor, "HIGH")
            breach_count += 1

    # Heart rate
    if hr is not None:
        if hr > vr["HR"]["CRITICAL_thresh_high"] or hr < vr["HR"]["CRITICAL_thresh_low"]:
            hits.append(RuleHit(
                rule_id="VR02", label=f"Heart rate critically abnormal ({hr} bpm)",
                floor="CRITICAL", source_ref="vitals:HR",
            ))
            floor = _merge(floor, "CRITICAL")
            breach_count += 1
        elif hr > vr["HR"]["HIGH_thresh_high"] or hr < vr["HR"]["HIGH_thresh_low"]:
            hits.append(RuleHit(
                rule_id="VR02", label=f"Heart rate abnormal ({hr} bpm)",
                floor="HIGH", source_ref="vitals:HR",
            ))
            floor = _merge(floor, "HIGH")
            breach_count += 1

    # Systolic BP
    if sbp is not None:
        if sbp > vr["SBP"]["CRITICAL_thresh_high"] or sbp < vr["SBP"]["CRITICAL_thresh_low"]:
            hits.append(RuleHit(
                rule_id="VR03", label=f"Systolic BP critically abnormal ({sbp} mmHg)",
                floor="CRITICAL", source_ref="vitals:SBP",
            ))
            floor = _merge(floor, "CRITICAL")
            breach_count += 1
        elif sbp > vr["SBP"]["HIGH_thresh_high"] or sbp < vr["SBP"]["HIGH_thresh_low"]:
            hits.append(RuleHit(
                rule_id="VR03", label=f"Systolic BP abnormal ({sbp} mmHg)",
                floor="HIGH", source_ref="vitals:SBP",
            ))
            floor = _merge(floor, "HIGH")
            breach_count += 1

    # Temperature
    if temp is not None:
        if temp > vr["TEMP"]["HIGH_thresh_high"] or temp < vr["TEMP"]["HIGH_thresh_low"]:
            hits.append(RuleHit(
                rule_id="VR04", label=f"Temperature abnormal ({temp}°C)",
                floor="HIGH", source_ref="vitals:TEMP",
            ))
            floor = _merge(floor, "HIGH")
            breach_count += 1

    # Two or more vitals breaches → CRITICAL (regardless of individual floor)
    if breach_count >= 2:
        floor = _merge(floor, "CRITICAL")

    return hits, floor
