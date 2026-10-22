"""
tests/test_conflict.py — Tests for conflict detection (Demo Case 2).
"""

import os
import json
import pytest
from conflict import detect_conflicts
from schemas import RefRegistry

def test_conflict_detection_case_2():
    # Case 2 input
    patient_text = "Mujhe koi bimari nahi hai, no diabetes, no medicines. Bas thodi kamzori hai."
    text_msgs = [(patient_text, "text:MSG_1")]
    
    ocr_lines = [
        ("HbA1c Glycated Hb 8.4 % 4.0 - 5.6 % HIGH", "ocr:OCR_LINE_2"),
        ("Tab. Metformin 500 mg BD", "ocr:OCR_LINE_5"),
    ]

    registry = RefRegistry()
    registry.register_text(1, patient_text)
    registry.register_ocr_line(2, ocr_lines[0][0])
    registry.register_ocr_line(5, ocr_lines[1][0])

    conflicts = detect_conflicts(text_msgs, ocr_lines, registry=registry)

    assert len(conflicts) >= 2
    # Verify refs
    refs_a = {c.source_a for c in conflicts}
    refs_b = {c.source_b for c in conflicts}

    assert "text:MSG_1" in refs_a or "text:MSG_1" in refs_b
    assert "ocr:OCR_LINE_2" in refs_a or "ocr:OCR_LINE_2" in refs_b
    assert "ocr:OCR_LINE_5" in refs_a or "ocr:OCR_LINE_5" in refs_b

    # Verify no diagnostic disease terms in summary
    for c in conflicts:
        assert "heart attack" not in c.summary.lower()
