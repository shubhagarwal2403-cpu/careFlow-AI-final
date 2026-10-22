"""
consent.py — CareFlow AI consent management
Consent text (EN + HI), recording, assertion, and case erasure.

Note: This consent text is a draft for prototype purposes only.
It has NOT been legally reviewed. Do not use in production without
review by a qualified legal professional.
"""

import hashlib
import json
import os
import shutil
import datetime
from typing import Optional

import db as _db
import audit


# ---------------------------------------------------------------------------
# Consent text
# ---------------------------------------------------------------------------

CONSENT_TEXT_EN = """
CAREFLOW AI — PATIENT INFORMATION AND CONSENT

[PROTOTYPE ONLY — NOT FOR CLINICAL USE — SYNTHETIC DATA ONLY]

What we collect:
• Your symptoms, health concerns, voice recordings, and uploaded medical reports
  that you choose to share during this intake session.
• Optional demographic information (age band, sex) — not required.
• No name, phone number, or national ID is collected.

How we use it:
• Your information is processed by an AI language model to help organise and
  prioritise your information for a reviewing clinician.
• This AI model is hosted by a third-party service (Groq Inc., USA). By
  consenting, you acknowledge that your intake information will be sent to
  this service for processing.
• A qualified clinician will review all AI outputs before any decision is made.
• This tool does NOT provide diagnoses. It organises information only.

Your rights:
• You may withdraw consent and request erasure of your data at any time during
  this session using the "Withdraw consent" button.
• After erasure, your clinical content will be deleted. A minimal anonymised
  record (no clinical content) is kept for audit purposes.

DPDP Act notice (India):
• This prototype is designed around principles of India's Digital Personal
  Data Protection Act 2023. It is a prototype and is not certified under any
  regulatory framework.

Limitations:
• This is a prototype using synthetic/demo data. It is NOT a medical device.
• Triage urgency levels are demo assumptions and are NOT clinically validated.
• Do not rely on this tool for any real medical decision.

By ticking the box below, you confirm that you have read and understood
this information and consent to the processing described above.
""".strip()

CONSENT_TEXT_HI = """
केयरफ्लो AI — रोगी सूचना और सहमति

[केवल प्रोटोटाइप — नैदानिक उपयोग के लिए नहीं — केवल सिंथेटिक डेटा]

हम क्या एकत्र करते हैं:
• आपके लक्षण, स्वास्थ्य संबंधी चिंताएं, आवाज रिकॉर्डिंग, और आपके द्वारा
  अपलोड की गई मेडिकल रिपोर्ट।
• वैकल्पिक जनसांख्यिकीय जानकारी (आयु वर्ग, लिंग) — आवश्यक नहीं।
• कोई नाम, फोन नंबर, या राष्ट्रीय पहचान पत्र एकत्र नहीं किया जाता।

हम इसका उपयोग कैसे करते हैं:
• आपकी जानकारी को एक AI भाषा मॉडल द्वारा संसाधित किया जाएगा ताकि
  एक चिकित्सक के लिए जानकारी को व्यवस्थित किया जा सके।
• यह AI मॉडल एक तृतीय-पक्ष सेवा (Groq Inc., USA) द्वारा होस्ट किया गया है।
• एक योग्य चिकित्सक सभी AI आउटपुट की समीक्षा करेगा।
• यह उपकरण कोई निदान प्रदान नहीं करता। यह केवल जानकारी व्यवस्थित करता है।

आपके अधिकार:
• आप किसी भी समय सहमति वापस ले सकते हैं और अपना डेटा मिटाने का अनुरोध
  कर सकते हैं।
• मिटाने के बाद, आपकी नैदानिक सामग्री हटा दी जाएगी।

सीमाएं:
• यह एक प्रोटोटाइप है। यह कोई चिकित्सा उपकरण नहीं है।
• ट्राइएज स्तर डेमो धारणाएं हैं और नैदानिक रूप से मान्य नहीं हैं।

नीचे दिए गए बॉक्स को चेक करके, आप पुष्टि करते हैं कि आपने यह जानकारी
पढ़ी और समझी है और ऊपर वर्णित प्रसंस्करण के लिए सहमति देते हैं।
""".strip()

CONSENT_TEXTS = {
    "en": CONSENT_TEXT_EN,
    "hi": CONSENT_TEXT_HI,
}


def _hash_consent_text(text: str) -> str:
    """SHA-256 of the consent text — stored so we know which version was shown."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Consent recording
# ---------------------------------------------------------------------------

def record_consent(
    case_id: str,
    language: str,
    consented_by: str,
    db_path: str = _db.DB_PATH,
    actor_role: str = "patient",
) -> None:
    """
    Record that consent was given for a case.
    Must be called before any intake function.
    """
    text = CONSENT_TEXTS.get(language, CONSENT_TEXT_EN)
    text_hash = _hash_consent_text(text)
    ts = datetime.datetime.utcnow().isoformat() + "Z"

    with _db.get_conn(db_path) as conn:
        conn.execute(
            """UPDATE cases SET
               consent_status = 'given',
               consent_timestamp = ?,
               consent_language = ?,
               consent_text_hash = ?,
               consented_by = ?
               WHERE id = ?""",
            (ts, language, text_hash, consented_by, case_id),
        )

    audit.log_action(
        actor_role=actor_role,
        action_type="consent_given",
        case_id=case_id,
        details={"consent_language": language},
        db_path=db_path,
    )


def assert_consent(case_id: str, db_path: str = _db.DB_PATH) -> None:
    """
    Raise ConsentRequired if the case does not have 'given' consent.
    Every intake function must call this first.
    """
    case = _db.get_case(case_id, db_path)
    if case is None:
        raise _db.CaseNotFound(f"Case {case_id} not found")
    if case["consent_status"] != "given":
        raise _db.ConsentRequired(
            f"Consent not given for case {case_id}. Status: {case['consent_status']}"
        )


# ---------------------------------------------------------------------------
# Erasure
# ---------------------------------------------------------------------------

def erase_case(
    actor_id: str,
    case_id: str,
    db_path: str = _db.DB_PATH,
) -> None:
    """
    Erase all clinical data for a case (consent withdrawal / right to erasure).

    Deletes:
    - intakes, ai_outputs, clinician_actions, the case row itself
    - Uploaded files in data/uploads/<case_id>/
    - Related session state (caller must clear st.session_state if needed)

    Keeps:
    - audit_logs rows (no PHI, append-only)
    - Writes one ERASURE audit record (hashed id + timestamp only)
    """
    # Check permission: patient (own case) or nurse
    actor = _db.get_user(actor_id, db_path)
    if actor is None:
        raise _db.PermissionDenied(f"Unknown user: {actor_id}")

    case = _db.get_case(case_id, db_path)
    if case is None:
        raise _db.CaseNotFound(f"Case {case_id} not found")

    if actor["role"] == "patient":
        if not _db.owns_case(actor_id, case_id, db_path):
            raise _db.PermissionDenied("Patients can only erase their own cases.")
    elif actor["role"] not in ("nurse",):
        raise _db.PermissionDenied(f"Role '{actor['role']}' cannot erase cases.")

    # Delete clinical data rows
    with _db.get_conn(db_path) as conn:
        conn.execute("DELETE FROM clinician_actions WHERE case_id = ?", (case_id,))
        conn.execute("DELETE FROM ai_outputs WHERE case_id = ?", (case_id,))
        conn.execute("DELETE FROM intakes WHERE case_id = ?", (case_id,))
        conn.execute("UPDATE cases SET status = 'erased', consent_status = 'withdrawn' WHERE id = ?", (case_id,))

    # Delete uploaded files
    uploads_dir = os.path.join(os.path.dirname(db_path), "data", "uploads", case_id)
    if os.path.isdir(uploads_dir):
        shutil.rmtree(uploads_dir)

    # Write the erasure audit record (no PHI — only hashed id + timestamp)
    audit.log_action(
        actor_role=actor["role"],
        action_type="erasure",
        case_id=case_id,
        details={"action": "erased"},
        db_path=db_path,
    )
