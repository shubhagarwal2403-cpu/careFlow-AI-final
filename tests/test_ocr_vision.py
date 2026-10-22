"""Offline tests for the vision OCR normaliser and portable path resolver."""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import ocr
from db import resolve_intake_path


def test_strip_json_fences_handles_markdown():
    raw = '```json\n{"lines": []}\n```'
    assert ocr._strip_json_fences(raw) == '{"lines": []}'


def test_normalise_bbox_pixels():
    # Pixel box from the model is preserved.
    assert ocr._normalise_bbox([40, 35, 380, 60], 700, 500) == [40, 35, 380, 60]


def test_normalise_bbox_normalised_falls_back_to_scaling():
    # 0-1000 normalised box overflows a small image and is rescaled.
    assert ocr._normalise_bbox([100, 100, 800, 800], 700, 500) == [70, 50, 560, 400]


def test_normalise_bbox_rejects_bad_values():
    assert ocr._normalise_bbox(None, 700, 500) is None
    assert ocr._normalise_bbox([0, 0, 0, 10], 700, 500) is None
    assert ocr._normalise_bbox(["x", 1, 2, 3], 700, 500) is None


def test_normalise_vision_lines_marks_low_confidence_unreadable():
    parsed = {
        "lines": [
            {"text": "HbA1c 8.4 %", "confidence": 0.99, "bbox": [10, 20, 100, 20]},
            {"text": "???", "confidence": 0.2, "unreadable": False},
            {"text": ""},
        ]
    }
    lines = ocr._normalise_vision_lines(parsed, 7, 700, 500)
    assert [ln["line_id"] for ln in lines] == ["OCR_LINE_7", "OCR_LINE_8"]
    assert lines[0]["unreadable"] is False
    assert lines[1]["unreadable"] is True


def test_auto_provider_prefers_precomputed_demo_json(tmp_path):
    report = tmp_path / "report.png"
    report.write_bytes(b"\x89PNG\r\n\x1a\nnot-a-real-image")
    (tmp_path / "ocr.json").write_text(
        '{"lines": [{"line_id": "OCR_LINE_1", "text": "HbA1c 8.4",'
        ' "bbox": [0, 0, 10, 10], "confidence": 0.99, "unreadable": false}]}',
        encoding="utf-8",
    )
    result = ocr.read_report(str(report), provider="auto")
    assert result["provider"] == "precomputed"
    assert result["lines"][0]["text"] == "HbA1c 8.4"


def test_resolve_intake_path_rewrites_stale_absolute_path():
    stale = "/Users/someone/Downloads/careflow-ai/careflow/data/demo_cases/conflict_report/report.png"
    resolved = resolve_intake_path(stale, "demo_conflict_report")
    assert os.path.exists(resolved)
    assert resolved.endswith(os.path.join("conflict_report", "report.png"))
