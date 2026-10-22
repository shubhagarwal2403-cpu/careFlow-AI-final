"""
ui_patient.py — CareFlow AI Patient intake page
Consent gate → intake form → follow-up questions → withdraw.
Patient NEVER sees urgency or AI triage output.
"""

import streamlit as st
import os
import uuid
import json
import html
import datetime

from db import (
    create_case, get_case, add_intake, next_msg_index,
    next_ocr_line_start, get_latest_ai_output, DB_PATH, ConsentRequired,
)
from consent import record_consent, assert_consent, erase_case, CONSENT_TEXTS
from followup import split_followup_questions
import audit


# ---------------------------------------------------------------------------
# Suicidal rule: calm support message (D14)
# ---------------------------------------------------------------------------
SUPPORT_MESSAGE = """
**You are not alone.** If you or someone you know is struggling,
please reach out to trained counsellors:

📞 **Tele-MANAS Helpline: 14416** (India, 24/7, free)

A clinician will review your intake shortly. Please stay with someone you trust.

*(Verify helpline number is current before real-world use.)*
"""

VALID_IMAGE_MAGIC = {
    b"\xff\xd8\xff": "image/jpeg",
    b"\x89PNG": "image/png",
    b"%PDF": "application/pdf",
}

# Audio formats accepted by the voice-note uploader. Checked by magic bytes
# first, then by file extension (some containers, e.g. M4A/WebM, are harder
# to fingerprint from the first few bytes).
VALID_AUDIO_MAGIC = {
    b"RIFF": "audio/wav",
    b"ID3": "audio/mpeg",
    b"\xff\xfb": "audio/mpeg",
    b"\xff\xf3": "audio/mpeg",
    b"\xff\xf2": "audio/mpeg",
    b"OggS": "audio/ogg",
    b"\x1aE\xdf\xa3": "audio/webm",
}
VALID_AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".ogg", ".webm", ".aac", ".flac"}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB


def _t(lang: str, english: str, hindi: str) -> str:
    """Small inline translation helper for patient-facing labels."""
    return english if lang == "en" else hindi


def _validate_file(uploaded_file) -> tuple[bool, str]:
    """Check file type by magic bytes/extension and size."""
    data = uploaded_file.read(16)
    uploaded_file.seek(0)
    size = uploaded_file.size

    if size > MAX_FILE_SIZE:
        return False, f"File too large ({size//1024//1024} MB, max 10 MB)"

    for magic, mime in VALID_IMAGE_MAGIC.items():
        if data.startswith(magic):
            return True, mime

    for magic, mime in VALID_AUDIO_MAGIC.items():
        if data.startswith(magic):
            return True, mime

    # MP4/M4A has the "ftyp" box marker at offset 4
    if data[4:8] == b"ftyp":
        return True, "audio/mp4"

    ext = os.path.splitext(uploaded_file.name or "")[1].lower()
    if ext in VALID_AUDIO_EXTENSIONS:
        return True, f"audio/{ext[1:]}"

    return False, "Unsupported file type. Please upload JPEG, PNG, PDF, or an audio file."


def _save_upload(uploaded_file, case_id: str, suffix: str) -> str:
    """Save an uploaded file to data/uploads/<case_id>/. Returns path."""
    upload_dir = os.path.join(os.path.dirname(DB_PATH), "data", "uploads", case_id)
    os.makedirs(upload_dir, exist_ok=True)
    ext = os.path.splitext(uploaded_file.name)[1] or ".bin"
    filename = f"{suffix}{ext}"
    path = os.path.join(upload_dir, filename)
    with open(path, "wb") as f:
        f.write(uploaded_file.getvalue())
    return path


def _highlight_safe(text: str, quote: str) -> str:
    """HTML-escape text, then wrap the quote in a highlight mark."""
    safe_text = html.escape(text)
    safe_quote = html.escape(quote)
    return safe_text.replace(safe_quote, f"<mark>{safe_quote}</mark>")


def _render_steps(current: int) -> None:
    """Render a compact 3-step intake progress indicator."""
    steps = [(1, "Consent"), (2, "Your health details"), (3, "Submitted")]
    chips = []
    for num, label in steps:
        if num < current:
            bg, border, color, icon = "rgba(16,185,129,0.15)", "rgba(16,185,129,0.45)", "#34d399", "✓"
        elif num == current:
            bg, border, color, icon = "rgba(99,102,241,0.18)", "rgba(99,102,241,0.55)", "#a5b4fc", str(num)
        else:
            bg, border, color, icon = "rgba(100,116,139,0.10)", "rgba(100,116,139,0.30)", "#64748b", str(num)
        chips.append(
            f'<span style="display:inline-flex;align-items:center;gap:7px;padding:5px 12px;'
            f'border-radius:999px;background:{bg};border:1px solid {border};color:{color};'
            f'font-size:0.78rem;font-weight:600;margin-right:6px;">'
            f'<span style="display:inline-flex;width:18px;height:18px;border-radius:50%;'
            f'background:{border};color:#0f172a;align-items:center;justify-content:center;'
            f'font-size:0.68rem;">{icon}</span>{html.escape(label)}</span>'
        )
    st.markdown(
        '<div style="margin:2px 0 18px 0;">' + "".join(chips) + "</div>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Virtual Hindi (Devanagari) keyboard
# ---------------------------------------------------------------------------
# Rows are ordered so a patient can type a consonant followed by a matra.
HINDI_KEYBOARD_ROWS = [
    ["अ", "आ", "इ", "ई", "उ", "ऊ", "ए", "ऐ", "ओ", "औ", "अं", "अः"],
    ["ा", "ि", "ी", "ु", "ू", "े", "ै", "ो", "ौ", "ं", "ः", "्"],
    ["क", "ख", "ग", "घ", "ङ", "च", "छ", "ज", "झ", "ञ", "ट", "ठ"],
    ["ड", "ढ", "ण", "त", "थ", "द", "ध", "न", "प", "फ", "ब", "भ"],
    ["म", "य", "र", "ल", "व", "श", "ष", "स", "ह", "ड़", "ढ़", "क्ष"],
    ["त्र", "ज्ञ", "ज़", "फ़", "क़", "ख़", "ग़", "०", "१", "२", "३", "४"],
    ["५", "६", "७", "८", "९", "।", ",", ".", "?", "!", "-", "("],
]


def _vk_append(target_key: str, text: str) -> None:
    """Append a character to the target text widget (button callback)."""
    st.session_state[target_key] = (st.session_state.get(target_key) or "") + text


def _vk_backspace(target_key: str) -> None:
    """Remove the last character from the target text widget."""
    st.session_state[target_key] = (st.session_state.get(target_key) or "")[:-1]


def _vk_clear(target_key: str) -> None:
    """Clear the target text widget."""
    st.session_state[target_key] = ""


def _render_hindi_keyboard(target_key: str) -> None:
    """Render a clickable Devanagari keyboard that types into ``target_key``.

    ``target_key`` must be the ``key`` of an ``st.text_area``/``st.text_input``.
    Button callbacks update that widget's session state before the next rerun,
    so clicking letters inserts them at the end of the current text.
    """
    st.markdown("⌨️ **हिन्दी कीबोर्ड / Hindi keyboard**")
    st.caption(
        "अक्षरों पर कर्सर से क्लिक करके लिखें। पहले व्यंजन, फिर मात्रा दबाएँ "
        "(जैसे: क + ा = का)."
    )
    with st.container(border=True):
        for r_idx, row in enumerate(HINDI_KEYBOARD_ROWS):
            cols = st.columns(len(row), gap="small")
            for c_idx, ch in enumerate(row):
                with cols[c_idx]:
                    st.button(
                        ch,
                        key=f"vkb_{target_key}_{r_idx}_{c_idx}",
                        on_click=_vk_append,
                        args=(target_key, ch),
                        use_container_width=True,
                    )

        c_space, c_back, c_clear = st.columns([2, 2, 1], gap="small")
        with c_space:
            st.button(
                "␣ स्पेस",
                key=f"vkb_{target_key}_space",
                on_click=_vk_append,
                args=(target_key, " "),
                use_container_width=True,
            )
        with c_back:
            st.button(
                "⌫ मिटाएँ",
                key=f"vkb_{target_key}_back",
                on_click=_vk_backspace,
                args=(target_key,),
                use_container_width=True,
            )
        with c_clear:
            st.button(
                "साफ़",
                key=f"vkb_{target_key}_clear",
                on_click=_vk_clear,
                args=(target_key,),
                use_container_width=True,
            )


def render_patient_page():
    """Main patient intake page."""
    user_id = st.session_state.get("user_id", "")
    user_role = st.session_state.get("user_role", "patient")
    user_name = st.session_state.get("user_name", "")
    if user_role == "nurse" and "nurse_on_behalf" not in st.session_state:
        st.session_state["nurse_on_behalf"] = True

    on_behalf = st.session_state.get("nurse_on_behalf", (user_role == "nurse"))

    # Header title
    if user_role == "nurse" and on_behalf:
        st.title("🩺 Nurse-Assisted Patient Intake")
        st.markdown(
            """
            <div style="background: rgba(99,102,241,0.12); border: 1px solid rgba(99,102,241,0.3); padding: 12px 18px; border-radius: 10px; margin-bottom: 20px;">
                <span style="font-weight: 600; color: #a5b4fc;">👥 Assisted Intake Mode: Active</span><br>
                <span style="font-size: 0.85rem; color: #cbd5e1;">Conducting intake for a patient unable to self-report (e.g. acute distress, trauma, elderly, or child).</span>
            </div>
            """,
            unsafe_allow_html=True,
        )
    else:
        st.title("🏥 Patient Intake")

    # A rerun is required after destructive actions, so surface one-shot
    # confirmations through session state instead of losing them.
    flash_success = st.session_state.pop("flash_success", None)
    if flash_success:
        st.success(flash_success)

    # Guard: if nurse explicitly disabled on_behalf mode, guide them
    if user_role == "nurse" and not on_behalf:
        st.info(
            "ℹ️ **CareFlow Workflow Note:** In this hospital workflow, patients typically complete their own intake. "
            "Nurses conduct intake when the patient is unable to self-report (e.g., severe distress, altered consciousness, elderly, or pediatric)."
        )
        if st.button("👥 Activate Nurse-Assisted Intake Mode", type="primary"):
            # Defer the sidebar widget update to the next run.
            st.session_state["_activate_nurse_mode"] = True
            st.rerun()
        return

    # ---- Language toggle ----
    lang = st.radio(
        "Language / भाषा",
        options=["en", "hi"],
        format_func=lambda x: "English" if x == "en" else "हिन्दी",
        horizontal=True,
        key="patient_lang",
    )

    # ---- Case init & persistence restoration from database ----
    if "current_case_id" not in st.session_state:
        st.session_state["current_case_id"] = None
        st.session_state["consent_given"] = False
        st.session_state["intake_submitted"] = False
        st.session_state["show_followup"] = False
        st.session_state["pipeline_error"] = None
        st.session_state["followup_done"] = False
        st.session_state.setdefault("proxy_form_nonce", 0)

    # For regular patients: restore an *unfinished* case after a page refresh.
    # Deliberately only restore cases that were never submitted: restoring a
    # completed case used to skip the consent screen and made the "Start New
    # Intake" button jump straight back into the old case (then crash).
    if user_role == "patient" and st.session_state.get("current_case_id") is None:
        from db import get_conn
        with get_conn() as conn:
            row = conn.execute(
                """SELECT id, pseudonym_code, status, consent_status, submitted_at
                   FROM cases
                   WHERE created_by = ? AND status != 'erased'
                     AND submitted_at IS NULL
                   ORDER BY rowid DESC LIMIT 1""",
                (user_id,)
            ).fetchone()
            if row:
                st.session_state["current_case_id"] = row["id"]
                st.session_state["consent_given"] = (row["consent_status"] == "given")
                st.session_state["intake_submitted"] = False

    case_id = st.session_state.get("current_case_id")

    # ---- Progress indicator ----
    _step = 1
    if st.session_state.get("consent_given", False):
        _step = 2
    if st.session_state.get("intake_submitted", False):
        _step = 3
    _render_steps(_step)

    # ---- Consent screen ----
    if not st.session_state.get("consent_given", False):
        if on_behalf:
            st.subheader("📋 Proxy Clinical Consent (Nurse-Assisted)")
            st.markdown(
                """
                **Attending Nurse Clinical Attestation:**
                - The patient cannot self-report due to physical, mental, or emergency incapacity.
                - Clinical triage consent is recorded via authorized nursing proxy under hospital intake protocol.
                """
            )
            nurse_patient_name = st.text_input(
                "Patient Full Name / Bed / ID" if lang == "en" else "रोगी का नाम / बेड / टोकन",
                placeholder="e.g. Ramesh Kumar, Bed 4, or Bay 2",
                key=f"nurse_proxy_patient_name_{st.session_state.get('proxy_form_nonce', 0)}",
            )
            proxy_cert = st.checkbox(
                "I certify clinical justification for nurse-assisted proxy intake",
                key=f"nurse_proxy_checkbox_{st.session_state.get('proxy_form_nonce', 0)}",
            )
            if st.button("Record Proxy Consent & Begin Intake", type="primary", disabled=not proxy_cert):
                new_case_id = str(uuid.uuid4())
                pseudonym = f"CASE-{new_case_id[:8].upper()}"
                pat_name = nurse_patient_name.strip() or "Assisted Patient"
                create_case(
                    case_id=new_case_id,
                    pseudonym_code=pseudonym,
                    created_by=user_id,
                    patient_name=pat_name,
                )
                record_consent(
                    case_id=new_case_id,
                    language=lang,
                    consented_by=user_id,
                    actor_role="nurse",
                )
                st.session_state["current_case_id"] = new_case_id
                st.session_state["consent_given"] = True
                st.session_state["intake_submitted"] = False
                st.session_state["pipeline_error"] = None
                st.session_state["followup_done"] = False
                # Fresh proxy form for the next patient
                st.session_state["proxy_form_nonce"] = st.session_state.get("proxy_form_nonce", 0) + 1
                st.rerun()
            return
        else:
            st.subheader("Patient Consent" if lang == "en" else "रोगी सहमति")

            consent_text = CONSENT_TEXTS.get(lang, CONSENT_TEXTS["en"])
            with st.expander("Read consent information" if lang == "en" else "सहमति जानकारी पढ़ें", expanded=True):
                st.text(consent_text)

            agreed = st.checkbox(
                "I have read and agree to the above" if lang == "en"
                else "मैंने ऊपर पढ़ा है और सहमत हूं",
                key="consent_checkbox",
            )

            if st.button("Proceed" if lang == "en" else "आगे बढ़ें", disabled=not agreed):
                new_case_id = str(uuid.uuid4())
                pseudonym = f"CASE-{new_case_id[:8].upper()}"
                create_case(
                    case_id=new_case_id,
                    pseudonym_code=pseudonym,
                    created_by=user_id,
                    patient_name=user_name or "Self-Report Patient",
                )
                record_consent(
                    case_id=new_case_id,
                    language=lang,
                    consented_by=user_id,
                    actor_role="patient",
                )
                st.session_state["current_case_id"] = new_case_id
                st.session_state["consent_given"] = True
                st.session_state["intake_submitted"] = False
                st.session_state["pipeline_error"] = None
                st.session_state["followup_done"] = False
                st.rerun()

            return  # Nothing else renders until consent

    # ---- Intake form ----
    case_id = st.session_state["current_case_id"]

    if not st.session_state.get("intake_submitted", False):
        st.subheader("Your Health Information" if lang == "en" else "आपकी स्वास्थ्य जानकारी")
        st.caption(
            "🔒 Your triage level and clinical notes are visible only to your care team."
            if lang == "en"
            else "🔒 आपका ट्रायाज स्तर और नैदानिक नोट्स केवल आपकी देखभाल टीम को दिखाई देते हैं।"
        )

        case = get_case(case_id)
        if case:
            st.info(
                f"**{_t(lang, 'Case code', 'केस कोड')}:** `{case['pseudonym_code']}`  \n"
                + _t(
                    lang,
                    "Share this with the nurse or doctor if needed.",
                    "ज़रूरत पड़ने पर यह कोड नर्स या डॉक्टर को दिखाएँ।",
                )
            )

        symptom_header, symptom_kb_toggle = st.columns([3, 1.35])
        with symptom_header:
            st.markdown("**Describe your symptoms**" if lang == "en" else "**अपने लक्षण बताएं**")
        with symptom_kb_toggle:
            show_keyboard = st.toggle(
                _t(lang, "⌨️ Hindi keyboard", "⌨️ हिन्दी कीबोर्ड"),
                value=(lang == "hi"),
                # Include the language in the key: switching to Hindi must
                # show the keyboard even if the English-mode toggle was off.
                key=f"show_hindi_kb_{case_id}_{lang}",
                help=_t(
                    lang,
                    "Type in Hindi/Devanagari without a Hindi keyboard. "
                    "Works even when the page is in English.",
                    "बिना हिन्दी कीबोर्ड के देवनागरी में लिखें। "
                    "अंग्रेज़ी पेज पर भी यह काम करता है।",
                ),
            )

        symptom_key = f"intake_symptom_{case_id}"
        symptom_text = st.text_area(
            "What brings you in today?" if lang == "en"
            else "आज क्या तकलीफ है?",
            height=120,
            placeholder=(
                "e.g. Sir, kal raat se left chest mein heavy pain ho raha hai..."
                if lang == "en"
                else "उदा. कल रात से सीने में भारी दर्द हो रहा है..."
            ),
            key=symptom_key,
        )

        # Visible and usable in nurse-assisted intake too (not only when the
        # whole page is switched to Hindi).
        if show_keyboard:
            _render_hindi_keyboard(symptom_key)

        st.markdown("**Voice note** (optional)" if lang == "en" else "**आवाज नोट** (वैकल्पिक)")
        audio_col1, audio_col2 = st.columns(2)
        with audio_col1:
            recorded_audio = st.audio_input(
                "🎤 Record live voice note" if lang == "en" else "🎤 लाइव आवाज़ रिकॉर्ड करें",
                key=f"audio_record_{case_id}",
            )
            st.caption(
                "Record directly from your microphone."
                if lang == "en"
                else "सीधे माइक्रोफ़ोन से रिकॉर्ड करें।"
            )
        with audio_col2:
            uploaded_audio = st.file_uploader(
                "📁 Upload audio (MP3, WAV, M4A)" if lang == "en" else "📁 ऑडियो अपलोड करें",
                type=["mp3", "wav", "m4a", "ogg", "webm"],
                key=f"audio_upload_{case_id}",
            )
            st.caption(
                "Or upload an existing recording."
                if lang == "en"
                else "या पहले से रिकॉर्ड की गई फ़ाइल अपलोड करें।"
            )

        st.markdown("**Upload report/document** (optional)" if lang == "en" else "**रिपोर्ट/दस्तावेज़ अपलोड करें** (वैकल्पिक)")
        uploaded_report = st.file_uploader(
            "Upload lab report, prescription or discharge summary (JPEG, PNG, PDF)"
            if lang == "en"
            else "लैब रिपोर्ट, नुस्खा या डिस्चार्ज सारांश अपलोड करें",
            type=["jpg", "jpeg", "png", "pdf"],
            key=f"report_upload_{case_id}",
            help=_t(
                lang,
                "Clearest possible photo or PDF, up to 10 MB. "
                "Blurry photos are flagged as unreadable rather than guessed.",
                "साफ़ फोटो या PDF, अधिकतम 10 MB। धुंधली फोटो को अनुमान लगाने के "
                "बजाय अपठनीय बताया जाएगा।",
            ),
        )
        st.caption(
            _t(
                lang,
                "Max 10 MB · JPG, PNG or PDF · bright, straight-on photos read best",
                "अधिकतम 10 MB · JPG, PNG या PDF · सीधी, अच्छी रोशनी वाली फोटो सबसे अच्छी पढ़ी जाती है",
            )
        )
        if uploaded_report:
            if (uploaded_report.type or "").lower() == "application/pdf":
                st.success(
                    _t(
                        lang,
                        f"📄 {uploaded_report.name} attached. The first page will be read by AI.",
                        f"📄 {uploaded_report.name} जुड़ी है। AI पहला पेज पढ़ेगा।",
                    )
                )
            else:
                st.image(
                    uploaded_report.getvalue(),
                    caption=_t(lang, "Report preview", "रिपोर्ट पूर्वावलोकन"),
                    width=280,
                )
            st.caption(
                _t(
                    lang,
                    "You can submit with only a report if you cannot type symptoms.",
                    "अगर आप लक्षण नहीं लिख सकते, तो सिर्फ रिपोर्ट अपलोड करके भी जमा कर सकते हैं।",
                )
            )

        # Optional vitals (nurse fills these)
        if user_role in ("nurse", "doctor"):
            st.markdown(
                _t(lang, "**Optional vitals** (nurse entry)", "**वैकल्पिक वाइटल्स** (नर्स प्रविष्टि)")
            )
            col1, col2, col3, col4 = st.columns(4)
            with col1:
                spo2 = st.number_input(
                    _t(lang, "SpO2 (%)", "ऑक्सीजन SpO2 (%)"),
                    min_value=50, max_value=100, value=None, step=1,
                )
            with col2:
                hr = st.number_input(
                    _t(lang, "Heart rate (bpm)", "हृदय गति (bpm)"),
                    min_value=10, max_value=300, value=None, step=1,
                )
            with col3:
                sbp = st.number_input(
                    _t(lang, "Systolic BP (mmHg)", "सिस्टोलिक बीपी (mmHg)"),
                    min_value=40, max_value=300, value=None, step=1,
                )
            with col4:
                temp = st.number_input(
                    _t(lang, "Temp (°C)", "तापमान (°C)"),
                    min_value=30.0, max_value=45.0, value=None, step=0.1,
                )
        else:
            spo2 = hr = sbp = temp = None

        # Patient Name & Demographics
        if user_role in ("nurse", "doctor"):
            curr_pn = (case or {}).get("patient_name") or ""
            if "sunita" in curr_pn.lower() or "nurse" in curr_pn.lower():
                curr_pn = "Assisted Patient"
            pat_name_field = st.text_input(
                "Patient Full Name / Bed / Identifier" if lang == "en" else "रोगी का नाम / बेड / पहचान",
                value=curr_pn,
                placeholder=_t(
                    lang,
                    "e.g. Ramesh Kumar, Bed 12, or Bay 3",
                    "उदा. रमेश कुमार, बेड 12, या बे 3",
                ),
                key=f"form_pat_name_{case_id}",
            )
        else:
            raw_u = user_name or "Self-Report Patient"
            pat_name_field = raw_u.replace("(Patient)", "").replace("(Nurse)", "").strip()

        col1, col2 = st.columns(2)
        with col1:
            age_band = st.selectbox(
                "Age band (optional)" if lang == "en" else "आयु वर्ग (वैकल्पिक)",
                ["", "0-10", "11-20", "21-30", "31-40", "41-50", "51-60", "61-70", "70+"],
                key=f"age_band_{case_id}",
            )
        with col2:
            sex = st.selectbox(
                "Sex (optional)" if lang == "en" else "लिंग (वैकल्पिक)",
                ["", "Male", "Female", "Other"],
                format_func=lambda v: {
                    "": "",
                    "Male": "Male" if lang == "en" else "पुरुष",
                    "Female": "Female" if lang == "en" else "महिला",
                    "Other": "Other" if lang == "en" else "अन्य",
                }[v],
                key=f"sex_{case_id}",
            )

        submitted = st.button(
            "Submit" if lang == "en" else "जमा करें",
            type="primary",
            use_container_width=True,
        )

        if submitted:
            if not symptom_text.strip() and not uploaded_audio and not recorded_audio and not uploaded_report:
                st.error(
                    _t(
                        lang,
                        "Please enter symptoms, upload/record audio, or upload a report.",
                        "कृपया लक्षण लिखें, ऑडियो अपलोड/रिकॉर्ड करें, या रिपोर्ट अपलोड करें।",
                    )
                )
                return

            saved_any = False

            # Add text intake
            if symptom_text.strip():
                msg_idx = next_msg_index(case_id)
                add_intake(
                    case_id=case_id,
                    channel="text",
                    content_text=symptom_text.strip(),
                    msg_index=msg_idx,
                )
                audit.log_action(
                    actor_role=user_role,
                    action_type="intake_submitted",
                    case_id=case_id,
                    details={"channel": "text"},
                )
                saved_any = True

            # Save uploaded and live-recorded audio (both are allowed)
            audio_items = []
            if uploaded_audio:
                audio_items.append((uploaded_audio, "voice", "Uploaded"))
            if recorded_audio:
                audio_items.append((recorded_audio, "voice_recorded", "Recorded"))
            for audio_file, suffix, label in audio_items:
                ok, mime = _validate_file(audio_file)
                if ok:
                    audio_path = _save_upload(audio_file, case_id, suffix)
                    add_intake(case_id=case_id, channel="voice", content_ref=audio_path)
                    saved_any = True
                else:
                    st.warning(f"{label} audio skipped: {mime}")

            # Save report upload
            if uploaded_report:
                ok, mime = _validate_file(uploaded_report)
                if ok:
                    ocr_start = next_ocr_line_start(case_id)
                    report_path = _save_upload(uploaded_report, case_id, "report")
                    add_intake(case_id=case_id, channel="ocr", content_ref=report_path, ocr_line_start=ocr_start)
                    saved_any = True
                else:
                    st.warning(f"Report skipped: {mime}")

            # Add vitals
            vitals = {}
            if spo2 is not None: vitals["SPO2"] = spo2
            if hr is not None: vitals["HR"] = hr
            if sbp is not None: vitals["SBP"] = sbp
            if temp is not None: vitals["TEMP"] = temp
            if vitals:
                add_intake(case_id=case_id, channel="vitals", content_text=json.dumps(vitals))
                saved_any = True

            if not saved_any:
                st.error(
                    "No usable intake content was saved. Please check the uploaded "
                    "file types and try again."
                )
                return

            # Update age/sex/patient_name on case
            from db import get_conn
            candidate_name = pat_name_field.strip() if (pat_name_field and pat_name_field.strip()) else ((case or {}).get("patient_name") or "Patient")
            candidate_name = candidate_name.replace("(Patient)", "").replace("(Nurse)", "").strip()
            if user_role == "nurse" and (not candidate_name or "sunita" in candidate_name.lower() or "nurse" in candidate_name.lower()):
                candidate_name = "Assisted Patient"
            final_pat_name = candidate_name or "Patient"
            with get_conn() as conn:
                conn.execute(
                    "UPDATE cases SET patient_name=?, age_band=?, sex=?, submitted_at=? WHERE id=?",
                    (final_pat_name, age_band or None, sex or None, datetime.datetime.utcnow().isoformat()+"Z", case_id),
                )

            # Run pipeline
            st.session_state["pipeline_error"] = None
            with st.spinner(_t(lang, "Processing...", "प्रोसेस हो रहा है...")):
                try:
                    from pipeline import process_case
                    result = process_case(
                        case_id,
                        ai_mode=st.session_state.get("ai_mode_override"),
                    )
                    st.session_state["pipeline_result"] = result
                    # Check for suicidal flag
                    hits = result.get("rule_hits", [])
                    st.session_state["show_support"] = any(
                        rh.get("rule_id") in ("R07", "R11") for rh in hits
                    )
                except Exception as e:
                    st.session_state["pipeline_error"] = str(e)
                    result = {}

            st.session_state["intake_submitted"] = True
            st.rerun()

    # ---- Post-submission (runs whenever intake_submitted is True) ----
    else:
        case = get_case(case_id)

        # Load the AI result defensively. After "Start New Intake" the key can
        # be None (or absent, e.g. after a browser refresh), and a submitted
        # case may only have its result in the database.
        pipe_res = st.session_state.get("pipeline_result") or {}
        if not pipe_res and case_id:
            try:
                latest = get_latest_ai_output(case_id)
                if latest:
                    pipe_res = json.loads(latest["output_json"])
                    st.session_state["pipeline_result"] = pipe_res
            except Exception:
                pipe_res = {}

        pipeline_error = st.session_state.get("pipeline_error")

        if pipeline_error:
            st.warning(
                _t(
                    lang,
                    "✅ **Your intake has been saved.**  \n"
                    "⚠️ Automated analysis could not be completed right now — a clinician "
                    "will review your intake manually.",
                    "✅ **आपकी जानकारी सुरक्षित है।**  \n"
                    "⚠️ अभी स्वचालित विश्लेषण पूरा नहीं हो सका — क्लिनिशियन "
                    "आपकी जानकारी की मैन्युअल समीक्षा करेंगे।",
                )
            )
            if user_role in ("nurse", "doctor", "admin"):
                with st.expander("Technical details (demo/debug)"):
                    st.code(str(pipeline_error))
        else:
            st.success(
                _t(
                    lang,
                    f"✅ **Intake Successfully Submitted & Analyzed.**  \n"
                    f"Case Code: **`{case['pseudonym_code'] if case else 'N/A'}`**",
                    f"✅ **जानकारी सफलतापूर्वक जमा और विश्लेषित हुई।**  \n"
                    f"केस कोड: **`{case['pseudonym_code'] if case else 'N/A'}`**",
                )
            )

        col_act1, col_act2 = st.columns(2)
        with col_act1:
            if on_behalf or user_role in ("nurse", "doctor"):
                if st.button(
                    _t(lang, "➕ Complete & Intake Next Patient", "➕ पूरा करें और अगला मरीज़"),
                    type="primary", use_container_width=True,
                ):
                    st.session_state["current_case_id"] = None
                    st.session_state["consent_given"] = False
                    st.session_state["intake_submitted"] = False
                    st.session_state["pipeline_result"] = None
                    st.session_state["pipeline_error"] = None
                    st.session_state["followup_done"] = False
                    st.session_state["show_support"] = False
                    st.rerun()
            else:
                if st.button(
                    _t(lang, "➕ Start New Intake Session", "➕ नया इनटेक शुरू करें"),
                    type="primary", use_container_width=True,
                ):
                    st.session_state["current_case_id"] = None
                    st.session_state["consent_given"] = False
                    st.session_state["intake_submitted"] = False
                    st.session_state["pipeline_result"] = None
                    st.session_state["pipeline_error"] = None
                    st.session_state["followup_done"] = False
                    st.session_state["show_support"] = False
                    st.rerun()

        with col_act2:
            if user_role in ("nurse", "doctor", "admin"):
                if st.button(
                    _t(lang, "📋 View Case in Clinical Queue →", "📋 क्लिनिकल क्यू में केस देखें →"),
                    type="secondary", use_container_width=True,
                ):
                    st.session_state["selected_case_id"] = case_id
                    st.session_state["clinician_view"] = "case"
                    # The sidebar page radio is already instantiated at this
                    # point in the run. Mutating its widget key directly raises
                    # StreamlitWidgetAlreadyInstantiatedError, so defer the
                    # navigation to the next run via a plain state key.
                    st.session_state["_pending_page"] = "Clinician Queue"
                    st.rerun()

        # Suicidal support message (D14)
        if st.session_state.get("show_support", False):
            st.warning(SUPPORT_MESSAGE)

        # Follow-up questions — asked at most ONCE per intake.
        # Re-running the AI on the answers produces a fresh set of questions
        # (sometimes different/fewer), which previously made the form loop
        # forever. Once the patient has answered, we incorporate the answers
        # and finish; no further questions are shown.
        result = st.session_state.get("pipeline_result") or {}
        slm_out = result.get("slm_output", {})
        questions = []
        if not st.session_state.get("followup_done"):
            questions = split_followup_questions(
                slm_out.get("follow_up_questions", [])
            )

        if questions:
            st.subheader("A few more questions / कुछ और सवाल")
            answers = {}
            question_texts = {}
            # The SLM is asked for atomic questions; allow enough slots that
            # splitting a compound question does not silently drop a part.
            # The cap only guards against a runaway form.
            for i, q in enumerate(questions[:8]):  # max 8
                q_text = q.get("question", "") if isinstance(q, dict) else str(q)
                question_texts[i] = q_text
                # Key by case so answers never carry over to the next patient.
                answers[i] = st.text_area(f"Q{i+1}: {q_text}", key=f"fup_{case_id}_{i}")

            # Hindi patients can answer with the clickable keyboard too; make
            # it available in English mode as well.
            if st.toggle(
                "⌨️ Hindi keyboard for answers / उत्तर हिन्दी में लिखें",
                value=(lang == "hi"),
                key=f"show_fup_hindi_kb_{case_id}_{lang}",
            ):
                kb_target = st.selectbox(
                    "कीबोर्ड किस उत्तर के लिए? / Keyboard target",
                    options=list(answers.keys()),
                    format_func=lambda i: f"Q{i+1}",
                    key=f"fup_kb_target_{case_id}",
                )
                _render_hindi_keyboard(f"fup_{case_id}_{kb_target}")

            fup_submitted = st.button(
                _t(lang, "Submit answers", "उत्तर जमा करें"),
                type="primary",
                use_container_width=True,
            )

            if fup_submitted:
                answered = {i: a.strip() for i, a in answers.items() if a and a.strip()}
                if not answered:
                    st.warning(
                        _t(
                            lang,
                            "Please answer at least one question before submitting.",
                            "कृपया जमा करने से पहले कम से कम एक उत्तर दें।",
                        )
                    )
                else:
                    for i, ans in answered.items():
                        msg_idx = next_msg_index(case_id)
                        add_intake(
                            case_id=case_id,
                            channel="text",
                            content_text=ans,
                            msg_index=msg_idx,
                            question=question_texts.get(i),
                        )
                    # Mark done BEFORE the rerun so the new AI output cannot
                    # trigger another round of questions.
                    st.session_state["followup_done"] = True
                    with st.spinner(_t(lang, "Updating...", "अपडेट हो रहा है...")):
                        try:
                            from pipeline import process_case
                            result = process_case(
                                case_id,
                                ai_mode=st.session_state.get("ai_mode_override"),
                            )
                            st.session_state["pipeline_result"] = result
                            st.session_state["pipeline_error"] = None
                        except Exception as e:
                            st.session_state["pipeline_error"] = str(e)
                    st.session_state["show_followup"] = False
                    st.rerun()

        if st.session_state.get("followup_done"):
            st.info(
                _t(
                    lang,
                    "✅ **Follow-up answers received.** Your intake is now complete — "
                    "a clinician will review it. No further questions are needed.",
                    "✅ **अतिरिक्त उत्तर मिल गए।** आपकी जानकारी पूरी है — "
                    "क्लिनिशियन इसकी समीक्षा करेंगे। और सवाल नहीं पूछे जाएँगे।",
                )
            )

        # Withdraw consent
        st.divider()
        st.subheader("Withdraw consent / सहमति वापस लें")
        if st.button("🗑️ Withdraw consent and delete my data", type="secondary"):
            st.session_state["confirm_erase"] = True

        if st.session_state.get("confirm_erase", False):
            st.warning("**This will permanently delete all your data.** This cannot be undone.")
            col1, col2 = st.columns(2)
            with col1:
                if st.button("Yes, delete everything", type="primary"):
                    try:
                        erase_case(actor_id=user_id, case_id=case_id)
                        st.session_state["current_case_id"] = None
                        st.session_state["consent_given"] = False
                        st.session_state["intake_submitted"] = False
                        st.session_state["confirm_erase"] = False
                        st.session_state["pipeline_result"] = None
                        st.session_state["pipeline_error"] = None
                        st.session_state["followup_done"] = False
                        st.session_state["show_support"] = False
                        st.session_state["flash_success"] = _t(
                            lang,
                            "Your data has been deleted.",
                            "आपका डेटा हटा दिया गया है।",
                        )
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error: {e}")
            with col2:
                if st.button("Cancel"):
                    st.session_state["confirm_erase"] = False
                    st.rerun()
