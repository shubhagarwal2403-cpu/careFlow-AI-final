"""Generate two synthetic lab-report samples for upload testing.

These are intentionally fake and contain no real patient data.
"""
import os

from PIL import Image, ImageDraw, ImageFont

OUT = os.path.join(os.path.dirname(__file__), "..", "data", "samples")
os.makedirs(OUT, exist_ok=True)

FONT = r"C:\Windows\Fonts\arial.ttf"
BOLD = r"C:\Windows\Fonts\arialbd.ttf"


def _font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


def make_report(title, subtitle, rows, footer, filename):
    width, height = 1100, 760
    img = Image.new("RGB", (width, height), "white")
    d = ImageDraw.Draw(img)
    f_title = _font(BOLD, 30)
    f_sub = _font(FONT, 18)
    f_head = _font(BOLD, 18)
    f_row = _font(FONT, 18)
    f_small = _font(FONT, 15)

    d.rectangle([25, 25, width - 25, height - 25], outline="#334155", width=2)
    d.text((50, 45), title, font=f_title, fill="#0f172a")
    d.text((50, 88), subtitle, font=f_sub, fill="#334155")
    d.line([50, 125, width - 50, 125], fill="#94a3b8", width=2)

    d.text((50, 145), "Patient: Ramesh Kumar    Age/Sex: 52 / Male", font=f_sub, fill="#0f172a")
    d.text((700, 145), "Report ID: LAB-2026-1042", font=f_sub, fill="#0f172a")
    d.text((50, 175), "Referred by: Dr. A. Mehta, MBBS", font=f_sub, fill="#334155")
    d.text((700, 175), "Collected: 21-Oct-2026", font=f_sub, fill="#334155")

    y = 225
    d.rectangle([50, y, width - 50, y + 38], fill="#e2e8f0")
    d.text((62, y + 8), "TEST NAME", font=f_head, fill="#0f172a")
    d.text((490, y + 8), "RESULT", font=f_head, fill="#0f172a")
    d.text((680, y + 8), "REFERENCE RANGE", font=f_head, fill="#0f172a")
    d.text((960, y + 8), "FLAG", font=f_head, fill="#0f172a")
    y += 50
    for name, result, ref, flag in rows:
        d.text((62, y), name, font=f_row, fill="#0f172a")
        d.text((490, y), result, font=f_row, fill="#0f172a")
        d.text((680, y), ref, font=f_row, fill="#334155")
        d.text((960, y), flag, font=f_row, fill="#b91c1c" if flag != "NORMAL" else "#166534")
        d.line([50, y + 30, width - 50, y + 30], fill="#e2e8f0", width=1)
        y += 42

    d.text((50, height - 120), footer, font=f_small, fill="#475569")
    d.text((50, height - 90), "This is a synthetic sample generated for CareFlow AI testing.", font=f_small, fill="#64748b")
    d.text((820, height - 80), "Dr. A. Mehta", font=f_sub, fill="#0f172a")
    d.text((820, height - 55), "Pathologist", font=f_small, fill="#475569")

    path = os.path.join(OUT, filename)
    img.save(path, quality=95)
    return path, img


cbc, cbc_img = make_report(
    "CITY DIAGNOSTIC LABORATORY",
    "Complete Blood Count (CBC) with ESR",
    [
        ("Hemoglobin (Hb)", "9.2 g/dL", "13.0 - 17.0", "LOW"),
        ("Total WBC Count", "13,500 /uL", "4,000 - 11,000", "HIGH"),
        ("Platelet Count", "1.4 lakh/uL", "1.5 - 4.5 lakh/uL", "LOW"),
        ("RBC Count", "4.1 million/uL", "4.5 - 5.5 million/uL", "LOW"),
        ("MCV", "78 fL", "80 - 100", "LOW"),
        ("ESR", "42 mm/hr", "0 - 20", "HIGH"),
    ],
    "Impression: Microcytic anemia with leucocytosis and thrombocytopenia. Clinical correlation advised.",
    "sample_cbc_report.png",
)

thyroid, thyroid_img = make_report(
    "METRO HEALTH LABORATORIES",
    "Thyroid Profile with Lipid Screen",
    [
        ("TSH", "8.9 uIU/mL", "0.4 - 4.0", "HIGH"),
        ("Free T4", "0.7 ng/dL", "0.8 - 1.8", "LOW"),
        ("Total Cholesterol", "240 mg/dL", "< 200", "HIGH"),
        ("LDL Cholesterol", "158 mg/dL", "< 100", "HIGH"),
        ("HbA1c", "6.8 %", "4.0 - 5.6", "HIGH"),
        ("Serum Creatinine", "1.0 mg/dL", "0.6 - 1.2", "NORMAL"),
    ],
    "Impression: Subclinical hypothyroidism with dyslipidemia and impaired glucose tolerance.",
    "sample_thyroid_report.png",
)

# Also make a PDF from the CBC report to exercise the PDF OCR path.
try:
    import fitz

    pdf_path = os.path.join(OUT, "sample_cbc_report.pdf")
    doc = fitz.open()
    page = doc.new_page(width=cbc_img.width * 0.75, height=cbc_img.height * 0.75)
    page.insert_image(page.rect, filename=cbc)
    doc.save(pdf_path)
    doc.close()
    print("pdf:", pdf_path)
except Exception as exc:  # pragma: no cover - only for local sample generation
    print("pdf generation skipped:", exc)

print("png:", cbc)
print("png:", thyroid)
