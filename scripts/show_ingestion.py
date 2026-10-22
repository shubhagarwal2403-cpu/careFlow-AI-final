"""
scripts/show_ingestion.py — Demonstrates speech & report ingestion across demo cases.
Prints transcript JSON, OCR JSON, guardrail scans, and detected conflicts.
"""

import os
import sys
import json

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from stt import transcribe
from ocr import read_report
from conflict import detect_conflicts
import guardrail

DEMO_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "demo_cases")

def show():
    print("==================================================")
    print("1. VOICE INGESTION DEMO: cardiac_hinglish")
    print("==================================================")
    audio_path = os.path.join(DEMO_DIR, "cardiac_hinglish", "voice.mp3")
    stt_res = transcribe(audio_path, provider="precomputed")
    print(f"Transcript: {stt_res['text']}")
    print(f"Segments Count: {len(stt_res['segments'])}")
    print(f"Low Confidence Segments: {stt_res['low_confidence_segments']}")
    print(f"Guardrail Flagged: {stt_res['guardrail']['flagged']}")

    print("\n==================================================")
    print("2. REPORT INGESTION DEMO: conflict_report")
    print("==================================================")
    img_path = os.path.join(DEMO_DIR, "conflict_report", "report.png")
    ocr_res = read_report(img_path, provider="precomputed")
    print(f"Extracted Lines: {len(ocr_res['lines'])}")
    for line in ocr_res['lines'][:3]:
        print(f"  [{line['line_id']}] (conf: {line['confidence']}) {line['text']}")

    # Conflict detection
    patient_text = "Mujhe koi bimari nahi hai, no diabetes, no medicines. Bas thodi kamzori hai."
    ocr_tuples = [(l["text"], l["line_id"]) for l in ocr_res["lines"]]
    conflicts = detect_conflicts([(patient_text, "text:MSG_1")], ocr_tuples)
    print(f"Detected Conflicts: {len(conflicts)}")
    for c in conflicts:
        print(f"  * {c.summary}")

    print("\n==================================================")
    print("3. DEGRADED REPORT DEMO: blurry_report")
    print("==================================================")
    blurry_path = os.path.join(DEMO_DIR, "blurry_report", "report.png")
    ocr_blurry = read_report(blurry_path, provider="precomputed")
    unreadable = [l for l in ocr_blurry["lines"] if l.get("unreadable")]
    print(f"Total lines: {len(ocr_blurry['lines'])}, Unreadable lines: {len(unreadable)}")
    for u in unreadable:
        print(f"  Unreadable: [{u['line_id']}] conf={u['confidence']} -> '{u['text']}'")

if __name__ == "__main__":
    show()
