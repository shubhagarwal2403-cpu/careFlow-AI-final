"""
scripts/try_text.py — CLI script for testing text triage directly.
Usage:
    python -m scripts.try_text "I have fever"
"""

import sys
import json
import uuid
import os

# Ensure careflow root in path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from db import init_db, create_case, add_intake, upsert_user
from consent import record_consent
from pipeline import process_case

def main():
    if len(sys.argv) < 2:
        print("Usage: python -m scripts.try_text \"your text message here\"")
        sys.exit(1)

    text_input = sys.argv[1]
    init_db()

    user_id = "test_cli_user"
    upsert_user(user_id, "CLI User", "patient")

    case_id = str(uuid.uuid4())
    create_case(case_id, f"CLI-{case_id[:6].upper()}", user_id)
    record_consent(case_id, "en", user_id)
    add_intake(case_id, "text", content_text=text_input, msg_index=1)

    result = process_case(case_id, ai_mode="auto")
    print(json.dumps(result, indent=2))

if __name__ == "__main__":
    main()
