"""
tests/test_ocr.py — Tests for the live Tesseract OCR path.

Pure path tests always run. The OCR execution tests are skipped when the
Tesseract binary is not installed on the machine.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from PIL import Image, ImageDraw, ImageFont

from ocr import ocr_sidecar_path, _pdf_page_image_path, read_report


def _tesseract_available() -> bool:
    try:
        import pytesseract
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


TEST_IMAGE = """
CITY CLINICAL LABORATORY REPORT
Test Name Result RefRange Flag
Fasting Blood Sugar 164mg/dL 70-100 HIGH
Hemoglobin 13.2 g/dL 12.0-15.0 NORMAL
Tab. Metformin 500 mg BD
"""


def _make_report_image(width: int = 1200, height: int = 500) -> Image.Image:
    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)
    font = None
    for candidate in (
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        if os.path.exists(candidate):
            font = ImageFont.truetype(candidate, 36)
            break
    draw.multiline_text(
        (30, 30), TEST_IMAGE.strip(), fill="black", spacing=14, font=font
    )
    return img


# ---------------------------------------------------------------------------
# Path handling (no Tesseract required)
# ---------------------------------------------------------------------------
def test_pdf_page_image_path_handles_uppercase_extension():
    path = "/tmp/reports/REPORT.PDF"
    assert _pdf_page_image_path(path) == "/tmp/reports/REPORT_page1.png"


def test_pdf_page_image_path_does_not_touch_parent_directories():
    path = "/tmp/a.pdf.dir/report.pdf"
    assert _pdf_page_image_path(path) == "/tmp/a.pdf.dir/report_page1.png"


def test_ocr_sidecar_path_is_unique_per_report_file():
    assert ocr_sidecar_path("/tmp/report.png") != ocr_sidecar_path("/tmp/report.pdf")
    assert ocr_sidecar_path("/tmp/report.png") == "/tmp/report.png.ocr.json"


# ---------------------------------------------------------------------------
# Live OCR execution
# ---------------------------------------------------------------------------
@pytest.mark.skipif(not _tesseract_available(), reason="Tesseract is not installed")
def test_tesseract_image_writes_sidecar(tmp_path):
    image_path = tmp_path / "report.png"
    _make_report_image().save(image_path)

    result = read_report(str(image_path), provider="tesseract")

    assert result["lines"], "Tesseract should extract at least one line"
    assert result["image_path"] == str(image_path)
    assert os.path.exists(ocr_sidecar_path(str(image_path)))


@pytest.mark.skipif(not _tesseract_available(), reason="Tesseract is not installed")
def test_tesseract_uppercase_pdf_is_not_overwritten(tmp_path):
    pdf_path = tmp_path / "REPORT.PDF"
    _make_report_image().save(pdf_path, "PDF")
    original_bytes = pdf_path.read_bytes()

    result = read_report(str(pdf_path), provider="tesseract")

    # The source PDF must still be a PDF, not PNG bytes written over it.
    assert pdf_path.read_bytes() == original_bytes
    assert pdf_path.read_bytes().startswith(b"%PDF")

    rendered = result["image_path"]
    assert rendered.endswith("_page1.png")
    assert os.path.exists(rendered)
    assert os.path.exists(ocr_sidecar_path(str(pdf_path)))


@pytest.mark.skipif(not _tesseract_available(), reason="Tesseract is not installed")
def test_tesseract_pdf_with_dot_pdf_in_parent_directory(tmp_path):
    report_dir = tmp_path / "a.pdf.dir"
    report_dir.mkdir()
    pdf_path = report_dir / "report.pdf"
    _make_report_image().save(pdf_path, "PDF")

    result = read_report(str(pdf_path), provider="tesseract")

    assert os.path.dirname(result["image_path"]) == str(report_dir)
    assert result["image_path"].endswith("report_page1.png")
    assert os.path.exists(result["image_path"])
