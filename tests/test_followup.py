"""
tests/test_followup.py — Tests for follow-up question splitting.
All offline.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from followup import split_question_text, split_followup_questions


# ---------------------------------------------------------------------------
# English lists and clause joins
# ---------------------------------------------------------------------------

def test_splits_english_or_list_with_shared_stem():
    assert split_question_text(
        "Are you experiencing any nausea, vision changes, or dizziness?"
    ) == [
        "Are you experiencing any nausea?",
        "Are you experiencing any vision changes?",
        "Are you experiencing any dizziness?",
    ]


def test_splits_four_item_list():
    assert split_question_text(
        "Do you also have shivering, chills, headache, or vomiting?"
    ) == [
        "Do you also have shivering?",
        "Do you also have chills?",
        "Do you also have headache?",
        "Do you also have vomiting?",
    ]


def test_splits_clause_join_into_independent_questions():
    assert split_question_text(
        "How many days have you had this fever, and did you measure your temperature?"
    ) == [
        "How many days have you had this fever?",
        "Did you measure your temperature?",
    ]


def test_splits_mixed_clause_then_list():
    assert split_question_text(
        "Has the headache worsened, or do you have any nausea or eye strain?"
    ) == [
        "Has the headache worsened?",
        "Do you have any nausea?",
        "Do you have any eye strain?",
    ]


def test_splits_adjective_alternatives():
    assert split_question_text("Is the pain constant or intermittent?") == [
        "Is the pain constant?",
        "Is the pain intermittent?",
    ]


def test_applies_trailing_location_to_every_item():
    assert split_question_text(
        "Do you have pain, swelling, or redness in your legs?"
    ) == [
        "Do you have pain in your legs?",
        "Do you have swelling in your legs?",
        "Do you have redness in your legs?",
    ]


def test_keeps_location_on_last_item_when_items_are_not_parallel():
    assert split_question_text(
        "Do you have fever, cough, or pain in the chest?"
    ) == [
        "Do you have fever?",
        "Do you have cough?",
        "Do you have pain in the chest?",
    ]


def test_multiple_sentences_become_separate_questions():
    assert split_question_text(
        "Do you have fever? How long have you had it?"
    ) == [
        "Do you have fever?",
        "How long have you had it?",
    ]


def test_splits_polite_prefix_embedded_questions():
    assert split_question_text(
        "Could you please tell me when the headache started, how long it has "
        "been going on, and how severe it feels on a scale of 1 to 10?"
    ) == [
        "Could you please tell me when the headache started?",
        "Could you please tell me how long it has been going on?",
        "Could you please tell me how severe it feels on a scale of 1 to 10?",
    ]


def test_splits_direct_comma_separated_questions():
    assert split_question_text(
        "When did the headache start, how long has it been going on, and "
        "how severe is it?"
    ) == [
        "When did the headache start?",
        "How long has it been going on?",
        "How severe is it?",
    ]


def test_splits_hinglish_direct_question_list():
    assert split_question_text(
        "Kya aapko fever hai, kab se hai, aur kitna dard hai?"
    ) == [
        "Kya aapko fever hai?",
        "Kab se hai?",
        "Kitna dard hai?",
    ]


# ---------------------------------------------------------------------------
# Hindi / Hinglish
# ---------------------------------------------------------------------------

def test_splits_hinglish_list():
    assert split_question_text(
        "Kya aapko nausea, vision changes ya dizziness hai?"
    ) == [
        "Kya aapko nausea hai?",
        "Kya aapko vision changes hai?",
        "Kya aapko dizziness hai?",
    ]


def test_splits_devanagari_list():
    result = split_question_text(
        "क्या आपको मतली, दृष्टि परिवर्तन या चक्कर आते हैं?"
    )
    assert len(result) == 3
    assert result[0].startswith("क्या आपको मतली")
    assert result[1].startswith("क्या आपको दृष्टि परिवर्तन")
    assert result[2].startswith("क्या आपको चक्कर")


def test_splits_hindi_polite_prefix():
    assert split_question_text(
        "कृपया बताइए कि यह सिरदर्द कब से शुरू हुआ है और कितने समय तक रहता है?"
    ) == [
        "कृपया बताइए कि यह सिरदर्द कब से शुरू हुआ है?",
        "कृपया बताइए कि कितने समय तक रहता है?",
    ]


# ---------------------------------------------------------------------------
# Conservative no-split cases
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "text",
    [
        # "left hand or jaw" is one question about radiation; splitting it
        # produces broken fragments.
        "Kya dard left haath ya jabde (jaw) ki taraf jaa raha hai?",
        # A chest-pain question followed by a different symptom is not a
        # parallel noun list.
        "Do you have pain in the chest or shortness of breath?",
        # No reliable shared stem; leave the compound intact.
        "Do you smoke or drink alcohol?",
        "Chest pain, shortness of breath?",
        # Already atomic.
        "Kya aap report ki ek saaf tasveer dobara bhej sakte hain?",
        "How long have you had these symptoms?",
        # A scale introduction is not one of the listed questions.
        "On a scale of 1 to 10, how severe would you rate your nighttime "
        "awakenings and associated symptoms?",
        # "such as ..." gives examples for one question, not separate ones.
        "Do you have any other symptoms such as chest pain, shortness of "
        "breath, or changes in appetite?",
        # The comma introduces the scale used by the single question.
        "कृपया बताइए कि दर्द कितनी तीव्र है, 1 से 10 के पैमाने पर?",
    ],
)
def test_leaves_ambiguous_questions_intact(text):
    assert split_question_text(text) == [text]


# ---------------------------------------------------------------------------
# Wrapper used by validate_slm_output
# ---------------------------------------------------------------------------

def test_wrapper_returns_schema_ready_dicts_and_dedupes():
    result = split_followup_questions(
        [
            {"question": "Do you have fever and cough?"},
            {"question": "Do you have fever?"},
            "Are you feeling tired?",
        ]
    )
    assert result == [
        {"question": "Do you have fever?"},
        {"question": "Do you have cough?"},
        {"question": "Are you feeling tired?"},
    ]


def test_wrapper_handles_empty_input():
    assert split_followup_questions([]) == []
    assert split_followup_questions(None) == []
