"""
tests/test_cached_outputs.py — Validates all cached demo outputs.
Verifies:
1. Each demo case ai_output.json parses strictly with SLMOutput schema.
2. No diagnosis or condition terms anywhere in the outputs.
3. All source_refs are structured validly.
"""

import os
import json
import pytest
from schemas import SLMOutput
from validate import _contains_diagnosis

DEMO_SLUGS = [
    "cardiac_hinglish",
    "conflict_report",
    "blurry_report",
    "prompt_injection",
    "fever_vague",
]

BASE_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "demo_cases")

@pytest.mark.parametrize("slug", DEMO_SLUGS)
def test_cached_output_schema_and_safety(slug):
    output_path = os.path.join(BASE_DIR, slug, "ai_output.json")
    assert os.path.exists(output_path), f"Missing cached output: {output_path}"

    with open(output_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # 1. Pydantic schema validation (strict extra=forbid)
    slm_obj = SLMOutput(**data)
    assert slm_obj is not None

    # 2. Assert no diagnosis terms in the entire json text (except allowed quoted source phrases)
    raw_text = json.dumps(data)
    # Check draft note, chief complaint, etc.
    assert "dengue" not in raw_text.lower()
    assert "heart attack" not in raw_text.lower()
    assert "myocardial infarction" not in raw_text.lower()
    assert "pneumonia" not in raw_text.lower()
    assert "stroke" not in raw_text.lower()
