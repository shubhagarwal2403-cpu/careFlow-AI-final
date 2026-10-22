"""
ui_clinician.py — CareFlow AI Clinician queue and case view
Queue table, case details, source grounding, alert box, sign-off panel.
This is the main page judges will see.
"""

import streamlit as st
import json
import html
import datetime
import os
from PIL import Image, ImageDraw
from io import BytesIO

from db import (
    list_cases_for_queue, get_case, get_latest_ai_output,
    record_clinician_action, get_clinician_actions, DB_PATH,
    resolve_intake_path,
)
from schemas import RefRegistry, SourceRef
from config import URGENCY_QUEUE_ORDER, UNCERTAIN_ESCALATE_MIN
from ocr import ocr_sidecar_path
import audit


# ---------------------------------------------------------------------------
# Urgency display helpers
# ---------------------------------------------------------------------------
URGENCY_COLORS = {
    "CRITICAL": "#dc2626",
    "HIGH": "#ea580c",
    "MEDIUM": "#d97706",
    "LOW": "#16a34a",
    "UNCERTAIN_NEEDS_FOLLOWUP": "#7c3aed",
}

URGENCY_LABELS = {
    "CRITICAL": "🚨 CRITICAL",
    "HIGH": "🔴 HIGH",
    "MEDIUM": "🟡 MEDIUM",
    "LOW": "🟢 LOW",
    "UNCERTAIN_NEEDS_FOLLOWUP": "❓ UNCERTAIN",
}


def _report_ocr_json_path(report_path: str) -> str:
    """Find live OCR output first, then fall back to demo/precomputed JSON."""
    sidecar = ocr_sidecar_path(report_path)
    if os.path.exists(sidecar):
        return sidecar
    return os.path.join(os.path.dirname(report_path), "ocr.json")


def _load_report_ocr(
    report_path: str, ocr_line_start: int = 1, case_id: str | None = None
) -> dict | None:
    """Load OCR JSON, backfilling reports uploaded before sidecars existed."""
    report_path = resolve_intake_path(report_path, case_id)
    ocr_json_path = _report_ocr_json_path(report_path)
    if os.path.exists(ocr_json_path):
        try:
            with open(ocr_json_path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass

    if not report_path or not os.path.exists(report_path):
        return None

    # Older uploads have no sidecar. Run OCR once now; read_report saves the
    # sidecar so subsequent views reuse the extraction instead of re-running it.
    try:
        from ocr import read_report
        return read_report(report_path, ocr_line_start=ocr_line_start or 1)
    except Exception:
        return None


def _urgency_chip(level: str) -> str:
    color = URGENCY_COLORS.get(level, "#64748b")
    label = URGENCY_LABELS.get(level, level)
    return (
        f'<span style="background:{color};color:#ffffff;padding:3px 10px;'
        f'border-radius:12px;font-size:0.8rem;font-weight:600;display:inline-block;">{html.escape(label)}</span>'
    )


_MISSING_LABELS = {
    "chief_complaint": "Chief complaint (what brings the patient in)",
    "age": "Age",
    "sex": "Sex",
    "duration": "Symptom duration",
    "severity": "Symptom severity",
    "vitals": "Vital signs",
    "temperature": "Temperature",
}


def _pretty_missing_label(item: object) -> str:
    """Turn model snake_case keys into labels a clinician can scan."""
    text = str(item).strip()
    mapped = _MISSING_LABELS.get(text.lower())
    if mapped:
        return mapped
    if "_" in text and " " not in text:
        return text.replace("_", " ").strip().capitalize()
    return text


def _missing_info_count(output_json: str | None) -> int:
    """Count missing-information items in a stored pipeline result."""
    if not output_json:
        return 0
    try:
        data = json.loads(output_json)
        return len(data.get("slm_output", {}).get("missing_information", []) or [])
    except Exception:
        return 0


def _wait_time(submitted_at: str) -> str:
    if not submitted_at:
        return "Unknown"
    try:
        then = datetime.datetime.fromisoformat(submitted_at.replace("Z", "+00:00"))
        now = datetime.datetime.now(datetime.timezone.utc)
        delta = now - then
        mins = int(delta.total_seconds() / 60)
        if mins < 60:
            return f"{mins} min"
        return f"{mins // 60}h {mins % 60}m"
    except Exception:
        return "Unknown"


def _rebuild_registry(result: dict) -> RefRegistry:
    """Re-create a RefRegistry from the pipeline result for UI grounding."""
    registry = RefRegistry()
    # We store refs implicitly; rebuild from result data for display
    slm = result.get("slm_output", {})

    # Add text refs from symptom timeline
    for st_item in slm.get("symptom_timeline", []):
        ref = st_item.get("source_ref", "")
        if ref.startswith("text:"):
            idx = ref.split("MSG_")[-1] if "MSG_" in ref else "1"
            # Text content comes from intakes; here we store the ref at minimum
            registry._refs[ref] = SourceRef(ref=ref, channel="text", text="(patient message)")

    return registry


def _draw_bbox_on_image(image_path: str, bbox: list, color: str = "red", label: str = "") -> bytes:
    """Draw a bounding box on an image. Returns PNG bytes."""
    try:
        img = Image.open(image_path).convert("RGB")
        draw = ImageDraw.Draw(img)
        x, y, w, h = bbox
        draw.rectangle([x, y, x + w, y + h], outline=color, width=3)
        if label:
            draw.text((x, max(0, y - 20)), label, fill=color)
        buf = BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception as e:
        return b""


def _highlight_text(text: str, quote: str) -> str:
    """HTML-escape text and highlight the evidence quote."""
    safe = html.escape(text)
    if quote:
        safe_quote = html.escape(quote)
        safe = safe.replace(
            safe_quote,
            f"<mark style='background:#fef08a;color:#1e293b;padding:2px 4px;border-radius:3px;font-weight:600;'>{safe_quote}</mark>"
        )
    return safe


def _render_all_uploaded_files(case_id: str):
    """Render all raw patient uploads: text messages, audio recordings, documents, and vitals."""
    from db import get_intakes
    intakes = get_intakes(case_id)
    if not intakes:
        st.info("No intake files found for this case.")
        return

    # Text intakes
    text_intakes = [i for i in intakes if i["channel"] == "text"]
    if text_intakes:
        st.markdown("##### 📝 Patient Text / Complaints & Follow-up Answers")
        for i, ti in enumerate(text_intakes):
            raw_text = ti["content_text"] or ""
            question = (ti.get("question") or "").strip()
            if question:
                # Show the follow-up question this text answers, so clinicians
                # can interpret short answers like "yes, since yesterday".
                st.markdown(
                    f'<div style="background:rgba(99,102,241,0.14);color:#e0e7ff;border:1px solid rgba(99,102,241,0.35);'
                    f'padding:10px 12px;border-radius:8px;font-size:0.85rem;line-height:1.4;margin-bottom:4px;">'
                    f'<strong>❓ Follow-up question:</strong> {html.escape(question)}</div>',
                    unsafe_allow_html=True,
                )
            st.markdown(
                f'<div style="background:rgba(30,41,59,0.7);color:#f1f5f9;border:1px solid rgba(99,102,241,0.25);'
                f'padding:12px;border-radius:8px;font-size:0.9rem;line-height:1.5;margin-bottom:8px;">'
                f'{html.escape(raw_text)}</div>',
                unsafe_allow_html=True,
            )

    # Voice / Audio intakes
    voice_intakes = [i for i in intakes if i["channel"] == "voice"]
    if voice_intakes:
        st.markdown("##### 🎙️ Voice Recordings & Transcripts")
        for i, vi in enumerate(voice_intakes):
            audio_path = resolve_intake_path(vi["content_ref"] or "", case_id)
            if audio_path and os.path.exists(audio_path):
                st.caption(f"Audio file: `{os.path.basename(audio_path)}`")
                try:
                    with open(audio_path, "rb") as af:
                        st.audio(af.read())
                except Exception as e:
                    st.warning(f"Could not load audio: {e}")

                # Live recordings cache their transcript next to the audio as
                # <name>.json; demo cases use a shared stt.json.
                audio_stem = os.path.splitext(audio_path)[0]
                stt_candidates = [
                    audio_stem + ".json",
                    os.path.join(os.path.dirname(audio_path), "stt.json"),
                ]
                stt_path = next((p for p in stt_candidates if os.path.exists(p)), None)
                if stt_path:
                    try:
                        with open(stt_path, encoding="utf-8") as f:
                            stt = json.load(f)
                        with st.expander("📄 View Audio Transcript", expanded=True):
                            segments = stt.get("segments", [])
                            if segments:
                                for seg in segments:
                                    low = seg.get("low_confidence", False)
                                    seg_text = html.escape(seg.get("text", ""))
                                    if low:
                                        seg_text = f'<span style="background:#fee2e2;color:#991b1b;padding:2px 6px;border-radius:3px;font-weight:600;">{seg_text}</span>'
                                    st.markdown(seg_text, unsafe_allow_html=True)
                            elif stt.get("text"):
                                st.markdown(html.escape(stt["text"]), unsafe_allow_html=True)
                    except Exception:
                        pass
            else:
                st.caption(f"Audio file: `{os.path.basename(audio_path) if audio_path else 'None'}` (not found on disk)")

    # OCR / Report intakes
    ocr_intakes = [i for i in intakes if i["channel"] == "ocr"]
    if ocr_intakes:
        st.markdown("##### 📄 Uploaded Documents & Reports")
        for i, oi in enumerate(ocr_intakes):
            report_path = resolve_intake_path(oi["content_ref"] or "", case_id)
            if report_path and os.path.exists(report_path):
                ext = os.path.splitext(report_path)[1].lower()
                st.caption(f"Document: `{os.path.basename(report_path)}`")
                if ext in [".jpg", ".jpeg", ".png"]:
                    st.image(report_path, width="stretch", caption=f"Uploaded {ext[1:].upper()} document")
                elif ext == ".pdf":
                    st.info(f"PDF Report attached: `{os.path.basename(report_path)}`")
                    # Show the first page inline so clinicians do not need a
                    # separate PDF reader just to sanity-check the upload.
                    base, _ = os.path.splitext(report_path)
                    preview_path = f"{base}_page1.png"
                    try:
                        if not os.path.exists(preview_path):
                            from ocr import _pdf_to_image
                            _pdf_to_image(report_path).save(preview_path)
                        if os.path.exists(preview_path):
                            with st.expander("🖼️ Preview first page", expanded=False):
                                st.image(
                                    preview_path,
                                    caption="PDF first page",
                                    width="stretch",
                                )
                    except Exception:
                        pass
                    try:
                        with open(report_path, "rb") as pf:
                            st.download_button(
                                label="📥 Download / View PDF Report",
                                data=pf.read(),
                                file_name=os.path.basename(report_path),
                                mime="application/pdf",
                                key=f"dl_pdf_{i}_{os.path.basename(report_path)}",
                            )
                    except Exception:
                        pass

                # Load OCR extraction (backfills older uploads on demand).
                ocr_data = _load_report_ocr(report_path, oi["ocr_line_start"] or 1, case_id)
                if ocr_data and ocr_data.get("lines"):
                    provider = ocr_data.get("provider") or "precomputed"
                    model = ocr_data.get("model")
                    label = f"{provider}" + (f" · {model}" if model else "")
                    st.caption(f"🔎 Extracted with: `{label}`")
                    with st.expander("🔍 View Extracted OCR Text"):
                        for line in ocr_data.get("lines", []):
                            unreadable = line.get("unreadable", False)
                            line_txt = html.escape(line.get("text", ""))
                            if unreadable:
                                st.markdown(f"⚠️ *[Unreadable]* `{line_txt}`")
                            else:
                                st.markdown(f"- {line_txt}")
                else:
                    st.caption("OCR text could not be extracted for this report.")
            else:
                st.caption(f"Report: `{os.path.basename(report_path) if report_path else 'None'}` (not found on disk)")

    # Vitals intakes
    vitals_intakes = [i for i in intakes if i["channel"] == "vitals"]
    if vitals_intakes:
        st.markdown("##### 🩺 Clinical Vitals")
        for vi in vitals_intakes:
            try:
                v = json.loads(vi["content_text"] or "{}")
                v_cols = st.columns(len(v) if v else 1)
                for idx, (vk, val) in enumerate(v.items()):
                    with v_cols[idx]:
                        st.metric(vk, val)
            except Exception:
                pass


def render_clinician_page():
    """Main clinician queue + case view."""
    st.title("👨‍⚕️ Clinician Dashboard")
    user_role = st.session_state.get("user_role", "nurse")
    user_id = st.session_state.get("user_id", "")

    if "clinician_view" not in st.session_state:
        st.session_state["clinician_view"] = "queue"
    if "selected_case_id" not in st.session_state:
        st.session_state["selected_case_id"] = None

    selected_id = st.session_state.get("selected_case_id")
    case = get_case(selected_id) if selected_id else None
    cases = list_cases_for_queue(DB_PATH)

    # If in case view but case missing, revert to queue
    if st.session_state["clinician_view"] == "case" and not case:
        st.session_state["clinician_view"] = "queue"

    # Top breadcrumb / navigation bar
    if st.session_state["clinician_view"] == "case" and case:
        col_back, col_title, col_switch = st.columns([2.5, 4.5, 3])
        with col_back:
            if st.button("← Back to Patient Queue", key="top_back_to_queue_btn", type="secondary", use_container_width=True):
                st.session_state["clinician_view"] = "queue"
                st.session_state["selected_ref"] = ""
                st.rerun()
        with col_title:
            # get_case() returns only the cases table, not the joined AI
            # urgency. Pull it from the queue row so the header cannot show
            # "UNCERTAIN" for a CRITICAL case.
            case_ai_urgency = next(
                (c.get("ai_urgency") for c in cases if c["id"] == case["id"]),
                None,
            )
            header_urgency = (
                case.get("final_urgency")
                or case_ai_urgency
                or "UNCERTAIN_NEEDS_FOLLOWUP"
            )
            st.markdown(
                f"<div style='margin-top:6px;font-size:1.1rem;font-weight:700;'>"
                f"Patient: <code>{case['pseudonym_code']}</code> "
                f"{_urgency_chip(header_urgency)}</div>",
                unsafe_allow_html=True,
            )
        with col_switch:
            case_ids = [c["id"] for c in cases]
            labels = {
                c["id"]: f"{c['pseudonym_code']} ({c.get('ai_urgency') or 'UNCERTAIN_NEEDS_FOLLOWUP'})"
                for c in cases
            }
            if selected_id in case_ids:
                curr_idx = case_ids.index(selected_id)
                switched_id = st.selectbox(
                    "Switch Patient",
                    options=case_ids,
                    index=curr_idx,
                    format_func=lambda cid: labels.get(cid, cid),
                    key="top_case_quick_switcher",
                    label_visibility="collapsed",
                )
                if switched_id != selected_id:
                    st.session_state["selected_case_id"] = switched_id
                    st.session_state["selected_ref"] = ""
                    st.rerun()
    else:
        st.caption("Active triage cases awaiting clinician review and sign-off")

    st.divider()

    # ================================================================
    # VIEW 1: Queue
    # ================================================================
    if st.session_state["clinician_view"] == "queue":
        if not cases:
            st.info("No cases in queue. Load demo cases from the Admin panel.")
            return

        # Count by urgency
        urgency_counts = {}
        for c in cases:
            urgency = c.get("ai_urgency") or "UNCERTAIN_NEEDS_FOLLOWUP"
            urgency_counts[urgency] = urgency_counts.get(urgency, 0) + 1

        # Header counts with theme-friendly background and high contrast
        count_cols = st.columns(5)
        for i, level in enumerate(URGENCY_QUEUE_ORDER):
            count = urgency_counts.get(level, 0)
            color = URGENCY_COLORS.get(level, "gray")
            label = URGENCY_LABELS.get(level, level)
            with count_cols[i]:
                st.markdown(
                    f'<div style="border-left:4px solid {color};padding:6px 10px;'
                    f'background:rgba(128,128,128,0.08);border-radius:0 6px 6px 0;">'
                    f'<strong style="color:{color};font-size:0.9rem;">{label}</strong><br>'
                    f'<span style="font-size:1.15rem;font-weight:700;">{count}</span> '
                    f'<span style="font-size:0.85rem;opacity:0.8;">case{"s" if count != 1 else ""}</span></div>',
                    unsafe_allow_html=True,
                )

        uncertain_count = urgency_counts.get("UNCERTAIN_NEEDS_FOLLOWUP", 0)
        missing_total = sum(_missing_info_count(c.get("output_json")) for c in cases)
        if uncertain_count or missing_total:
            bits = []
            if uncertain_count:
                bits.append(
                    f"❓ {uncertain_count} UNCERTAIN case{'s' if uncertain_count != 1 else ''}"
                )
            if missing_total:
                bits.append(f"🧩 {missing_total} missing-information item{'s' if missing_total != 1 else ''}")
            st.warning(" · ".join(bits) + " — review these before deciding.")

        st.divider()

        refresh_col, updated_col = st.columns([1, 5])
        with refresh_col:
            if st.button("🔄 Refresh queue", type="secondary", use_container_width=True):
                st.rerun()
        with updated_col:
            st.caption(
                f"Queue updated {datetime.datetime.now().strftime('%H:%M:%S')} · "
                "new submissions appear after refresh"
            )

        col_sort1, col_sort2, col_search = st.columns([1.55, 1.0, 1.25])
        with col_sort1:
            st.markdown("<div style='font-size:0.83rem; font-weight:600; color:#94a3b8; margin-bottom:6px;'>Filter Queue</div>", unsafe_allow_html=True)
            queue_filter = st.radio(
                "Filter Queue",
                options=["All Cases", "New & Live Intakes", "Demo Benchmark Cases"],
                horizontal=True,
                label_visibility="collapsed",
                key="queue_filter_mode",
            )
        with col_sort2:
            st.markdown("<div style='font-size:0.83rem; font-weight:600; color:#94a3b8; margin-bottom:6px;'>Sort Queue by</div>", unsafe_allow_html=True)
            sort_by = st.selectbox(
                "Sort Queue by",
                options=["Newest First", "Most Urgent First"],
                index=0,
                label_visibility="collapsed",
                key="queue_sort_order",
            )
        with col_search:
            st.markdown("<div style='font-size:0.83rem; font-weight:600; color:#94a3b8; margin-bottom:6px;'>Find a patient</div>", unsafe_allow_html=True)
            search_q = st.text_input(
                "Search queue",
                placeholder="🔍 Name, case code or symptom",
                label_visibility="collapsed",
                key="queue_search",
            )

        # Apply filter
        display_cases = cases
        if queue_filter == "New & Live Intakes":
            display_cases = [c for c in cases if not c.get("ai_cached")]
        elif queue_filter == "Demo Benchmark Cases":
            display_cases = [c for c in cases if c.get("ai_cached")]

        if search_q and search_q.strip():
            needle = search_q.strip().lower()
            display_cases = [
                c for c in display_cases
                if needle in str(c.get("patient_name") or "").lower()
                or needle in str(c.get("pseudonym_code") or "").lower()
                or needle in str(c.get("raw_symptom_text") or "").lower()
            ]

        if not display_cases:
            st.info("No cases matching the selected filter or search.")

        # Sort cases
        if sort_by == "Newest First":
            sorted_cases = sorted(display_cases, key=lambda c: c.get("submitted_at") or "", reverse=True)
        else:
            def _sort_key(c):
                urgency = c.get("ai_urgency") or "UNCERTAIN_NEEDS_FOLLOWUP"
                idx = URGENCY_QUEUE_ORDER.index(urgency) if urgency in URGENCY_QUEUE_ORDER else 99
                # Sub-sort newest first within the same urgency level
                submitted = c.get("submitted_at") or ""
                try:
                    ts = -datetime.datetime.fromisoformat(
                        submitted.replace("Z", "+00:00")
                    ).timestamp() if submitted else 0
                except Exception:
                    ts = 0
                return (idx, ts)
            sorted_cases = sorted(display_cases, key=_sort_key)

        st.caption(
            f"Showing **{len(sorted_cases)}** of **{len(cases)}** cases"
            + (f" · filter: {queue_filter}" if queue_filter != "All Cases" else "")
            + (f" · search: “{search_q.strip()}”" if search_q and search_q.strip() else "")
        )

        for c in sorted_cases:
            case_id = c["id"]
            urgency = c.get("ai_urgency") or "UNCERTAIN_NEEDS_FOLLOWUP"
            wait = _wait_time(c.get("submitted_at", ""))
            cached = c.get("ai_cached", 0)

            # Parse pipeline result for badges
            result = {}
            output_json = c.get("output_json")
            if output_json:
                try:
                    result = json.loads(output_json)
                except Exception:
                    pass

            slm = result.get("slm_output", {})
            has_conflict = bool(slm.get("conflicts"))
            has_unclear = bool(slm.get("uncertainty_flags"))
            missing_info_count = len(slm.get("missing_information", []) or [])
            has_injection = result.get("injection_detected", False)
            rule_hits = result.get("rule_hits", [])
            has_urgent_signal = any(not rh.get("negated") for rh in rule_hits)
            unreadable_ocr = any(
                f.get("issue_type") == "UNREADABLE_OCR"
                for f in slm.get("uncertainty_flags", [])
            )
            
            # Smart description fallback — never show "Unable to extract"
            chief = (slm.get("chief_complaint") or {}).get("text", "")
            if (
                not chief
                or "Unable to extract" in chief
                or chief == "No description"
                or str(chief).strip().upper() in {"UNKNOWN", "NONE", "N/A"}
            ):
                raw_sym = c.get("raw_symptom_text")
                if raw_sym:
                    chief = raw_sym.strip().replace("\n", " ")
                elif c.get("has_report"):
                    chief = "Report-only intake - no symptom description provided"
                else:
                    chief = "Clinical intake recorded - awaiting clinician review"

            # Check if case was submitted recently (< 30 min)
            is_new = False
            sub_at = c.get("submitted_at")
            if sub_at:
                try:
                    sub_dt = datetime.datetime.fromisoformat(sub_at.replace("Z", "+00:00"))
                    if (datetime.datetime.now(datetime.timezone.utc) - sub_dt).total_seconds() < 1800:
                        is_new = True
                except Exception:
                    pass

            # Amber wait marker for uncertain cases
            amber = ""
            if urgency == "UNCERTAIN_NEEDS_FOLLOWUP":
                try:
                    then = datetime.datetime.fromisoformat(
                        (c.get("submitted_at") or "").replace("Z", "+00:00")
                    )
                    mins = int((datetime.datetime.now(datetime.timezone.utc) - then).total_seconds() / 60)
                    if mins >= UNCERTAIN_ESCALATE_MIN:
                        amber = f"⚠️ Waiting {mins}m"
                except Exception:
                    pass

            creator_s = c.get("creator_name") or "Self"
            is_nurse_created = ("nurse" in str(c.get("created_by")).lower()) or ("nurse" in creator_s.lower())
            
            raw_pat = c.get("patient_name") or ""
            # If nurse took it and no separate patient name was typed, don't show nurse's name as patient!
            if is_nurse_created and (not raw_pat or "sunita" in raw_pat.lower() or "nurse" in raw_pat.lower()):
                pat_display = "Assisted Patient"
            elif raw_pat:
                pat_display = raw_pat
            else:
                pat_display = "Patient"

            pat_clean = pat_display.replace("(Patient)", "").replace("(Nurse)", "").strip()
            creator_clean = creator_s.replace("(Nurse)", "").replace("(Patient)", "").strip()
            
            patient_role_tab = '<span style="background:rgba(59,130,246,0.18); color:#93c5fd; border:1px solid rgba(59,130,246,0.35); border-radius:12px; padding:1px 7px; font-size:0.68rem; font-weight:600; text-transform:uppercase; margin-left:4px;">Patient</span>'
            
            if is_nurse_created:
                intake_role_tab = '<span style="background:rgba(168,85,247,0.18); color:#d8b4fe; border:1px solid rgba(168,85,247,0.35); border-radius:12px; padding:1px 6px; font-size:0.68rem; font-weight:600; text-transform:uppercase; margin-left:3px;">Nurse</span>'
                staff_display = f"Intake by: <strong style='color:#a5b4fc;'>{html.escape(creator_clean)}</strong> {intake_role_tab}"
            else:
                staff_display = f"Intake: <strong style='color:#a5b4fc;'>Self-Report</strong>"

            age_s = c.get("age_band") or "unspecified"
            sex_s = c.get("sex") or "unspecified"

            with st.container():
                st.markdown(
                    f"""
                    <div style="background: linear-gradient(135deg, rgba(30, 41, 59, 0.5) 0%, rgba(15, 23, 42, 0.45) 100%); 
                                border: 1px solid rgba(99, 102, 241, 0.22);
                                border-radius: 12px; padding: 12px 16px; margin-bottom: 8px;">
                        <div style="display: flex; justify-content: space-between; align-items: center;">
                            <div style="display: flex; align-items: center; gap: 8px;">
                                <span style="font-size: 1.05rem; font-weight: 700; color: #f8fafc;">👤 {html.escape(pat_clean)}</span>
                                {patient_role_tab}
                                <span style="background: rgba(99, 102, 241, 0.2); color: #c7d2fe; border: 1px solid rgba(99, 102, 241, 0.35); padding: 1px 7px; border-radius: 6px; font-weight: 600; font-size: 0.78rem; margin-left:4px;">{c['pseudonym_code']}</span>
                                <span style="color: #64748b; font-size: 0.72rem;">(ID: {case_id[:8]})</span>
                            </div>
                            <div style="font-size: 0.8rem; color: #94a3b8;">
                                ⏱️ Wait: <strong style="color:#f1f5f9;">{wait}</strong> · {staff_display}
                            </div>
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

                col1, col2, col3, col4 = st.columns([2, 4.2, 2.3, 1.5])
                with col1:
                    st.markdown(_urgency_chip(urgency), unsafe_allow_html=True)
                    if amber:
                        st.markdown(f"<small style='color:#d97706;font-weight:600;'>{amber}</small>", unsafe_allow_html=True)
                with col2:
                    # Badges
                    badges = []
                    if is_new: badges.append("✨ NEW")
                    if has_urgent_signal: badges.append("🚨 Urgent")
                    if has_conflict: badges.append("⚠️ Conflict")
                    if has_unclear: badges.append("❓ Incomplete")
                    if missing_info_count: badges.append(f"🧩 Missing info ({missing_info_count})")
                    if has_injection: badges.append("🛡 Suspicious")
                    if unreadable_ocr: badges.append("📄 Unreadable report")
                    if cached:
                        badges.append("💾 Demo Case")
                    else:
                        badges.append("⚡ Live AI")

                    st.markdown(" ".join(f"`{b}`" if not b.startswith("✨") else f"**{b}**" for b in badges))
                    st.markdown(f"<div style='font-size:0.86rem; color:#cbd5e1; margin-top:3px;'><strong>Chief Complaint:</strong> {html.escape(str(chief)[:110])}</div>", unsafe_allow_html=True)
                with col3:
                    st.caption(f"Demographics: Age {age_s} · Sex {sex_s}")
                    final = c.get("final_urgency")
                    if final:
                        st.markdown(f"<span style='color:#10b981;font-weight:600;font-size:0.82rem;'>✅ Signed off ({URGENCY_LABELS.get(final, final)})</span>", unsafe_allow_html=True)
                    else:
                        st.markdown("<span style='color:#eab308;font-size:0.8rem;'>⏳ Pending Sign-off</span>", unsafe_allow_html=True)
                with col4:
                    if st.button("Review →", key=f"view_{case_id}", use_container_width=True, type="primary"):
                        st.session_state["selected_case_id"] = case_id
                        st.session_state["clinician_view"] = "case"
                        st.session_state["selected_ref"] = ""
                        st.rerun()

            st.divider()

    # ================================================================
    # VIEW 2: Case View
    # ================================================================
    else:
        selected_id = st.session_state.get("selected_case_id")
        if not selected_id:
            st.info("Select a case from the queue.")
            return

        case = get_case(selected_id)
        if not case:
            st.error("Case not found.")
            return

        # Patient Profile Banner
        creator_h = case.get("creator_name") or "Self"
        is_nurse_created_h = ("nurse" in str(case.get("created_by")).lower()) or ("nurse" in creator_h.lower())
        raw_pat_h = case.get("patient_name") or ""
        if is_nurse_created_h and (not raw_pat_h or "sunita" in raw_pat_h.lower() or "nurse" in raw_pat_h.lower()):
            pat_name_header = "Assisted Patient"
        elif raw_pat_h:
            pat_name_header = raw_pat_h
        else:
            pat_name_header = "Patient"

        pat_clean_h = pat_name_header.replace("(Patient)", "").replace("(Nurse)", "").strip()
        creator_clean_h = creator_h.replace("(Nurse)", "").replace("(Patient)", "").strip()

        sub_time = case.get('submitted_at') or 'Time not recorded'
        if "T" in sub_time:
            sub_time = sub_time.split("T")[0] + " " + sub_time.split("T")[1][:5] + " UTC"

        st.markdown(
            f"""
            <div style="background: linear-gradient(135deg, rgba(30, 41, 59, 0.7) 0%, rgba(15, 23, 42, 0.7) 100%); 
                        border: 1px solid rgba(99, 102, 241, 0.28); border-radius: 12px; padding: 16px 22px; margin-bottom: 16px;">
                <div style="display: flex; justify-content: space-between; align-items: flex-start; flex-wrap: wrap; gap: 10px;">
                    <div>
                        <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 4px;">
                            <span style="font-size: 1.35rem; font-weight: 700; color: #f8fafc;">👤 {html.escape(pat_clean_h)}</span>
                            <span style="background:rgba(59,130,246,0.18); color:#93c5fd; border:1px solid rgba(59,130,246,0.35); border-radius:12px; padding:2px 8px; font-size:0.72rem; font-weight:600; text-transform:uppercase;">Patient</span>
                        </div>
                        <div style="font-size: 0.85rem; color: #94a3b8;">
                            Case Code: <strong style="color:#a5b4fc;">{case.get('pseudonym_code')}</strong> · 
                            System ID: <code style="color:#cbd5e1; font-size:0.78rem;">{selected_id}</code>
                        </div>
                    </div>
                    <div style="text-align: right; font-size: 0.82rem; color: #94a3b8;">
                        <div>🏥 <strong>Demographics:</strong> Age {case.get('age_band') or 'unspecified'} · Sex {case.get('sex') or 'unspecified'}</div>
                        <div>📅 Registered: {sub_time}</div>
                        <div>🩺 Intake Staff: <strong style="color:#c7d2fe;">{html.escape(creator_clean_h)}</strong>{' <span style="background:rgba(168,85,247,0.18); color:#d8b4fe; border:1px solid rgba(168,85,247,0.35); border-radius:12px; padding:1px 6px; font-size:0.68rem; font-weight:600; text-transform:uppercase;">Nurse</span>' if is_nurse_created_h else ''}</div>
                    </div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        ai_output = get_latest_ai_output(selected_id)
        if not ai_output:
            st.warning("No AI output yet for this case.")
            return

        try:
            result = json.loads(ai_output["output_json"])
        except Exception:
            st.error("Could not parse AI output.")
            return

        slm = result.get("slm_output", {})
        rule_hits = result.get("rule_hits", [])
        security_flags = result.get("security_flags", [])
        meta = result.get("meta", {})
        final_urgency = result.get("final_suggested_urgency", "UNCERTAIN_NEEDS_FOLLOWUP")
        cached = meta.get("cached", False)

        # ---- Alert box ----
        with st.container():
            alerts = []
            if cached:
                alerts.append("💾 **DEMO BENCHMARK CASE** — Precomputed baseline case loaded for demonstration.")
            else:
                ai_model = meta.get("model", "SLM")
                latency_s = round(meta.get("latency_ms", 0) / 1000, 2)
                alerts.append(f"⚡ **LIVE AI TRIAGE** — Evaluated in real time via `{ai_model}` ({latency_s}s response).")

            if security_flags:
                for sf in security_flags:
                    alerts.append(f"🛡 **Suspicious input detected** on {sf.get('channel', '?')} channel: `{html.escape(str(sf.get('matched_span', ''))[:50])}`")
            if slm.get("conflicts"):
                for conf in slm["conflicts"]:
                    sa = conf.get("source_a", "")
                    sb = conf.get("source_b", "")
                    summary = html.escape(str(conf.get("summary", ""))[:120])
                    alerts.append(f"⚠️ **Conflict:** {summary}  \n[Source A: {sa}] vs [Source B: {sb}]")
            for uf in slm.get("uncertainty_flags", []):
                if uf.get("issue_type") == "UNREADABLE_OCR":
                    alerts.append(f"📄 **Report difficult to read** — Check original image. {html.escape(uf.get('reason', '')[:80])}")
            for uf in slm.get("uncertainty_flags", []):
                if uf.get("issue_type") == "LOW_AUDIO_CONFIDENCE":
                    alerts.append(f"🎙️ **Low audio confidence** — Some words may be mis-transcribed. {html.escape(uf.get('reason', '')[:80])}")

            if alerts:
                for a in alerts:
                    if "LIVE AI" in a:
                        st.info(a)
                    elif "DEMO BENCHMARK" in a:
                        st.caption(a)
                    else:
                        st.error(a)

        # ---- Two-column layout ----
        left, right = st.columns([3, 2])

        with left:
            # Urgency
            color = URGENCY_COLORS.get(final_urgency, "#64748b")
            label = URGENCY_LABELS.get(final_urgency, final_urgency)
            st.markdown(
                f"**AI Suggested Urgency:** "
                f'<span style="background:{color};color:#ffffff;padding:3px 10px;'
                f'border-radius:12px;font-weight:bold;display:inline-block;">{html.escape(label)}</span>',
                unsafe_allow_html=True,
            )

            # Chief complaint
            cc = slm.get("chief_complaint") or {}
            cc_text = cc.get("text", "")
            cc_is_fallback = False
            if (
                not cc_text
                or "Unable to extract" in cc_text
                or str(cc_text).strip().upper() in {"UNKNOWN", "NONE", "N/A"}
            ):
                cc_is_fallback = True
                from db import get_intakes
                intakes = get_intakes(selected_id)
                raw_txt = next((i["content_text"] for i in intakes if i.get("channel") == "text" and i.get("content_text")), "")
                if raw_txt:
                    cc_text = raw_txt.strip()
                elif any(i.get("channel") == "ocr" for i in intakes):
                    cc_text = "Report-only intake — no symptom description provided"
                else:
                    cc_text = "Clinical intake recorded — pending clinician review"

            st.markdown(f"**Chief Complaint:** {html.escape(str(cc_text))}")
            if cc.get("source_ref") and not cc_is_fallback:
                if st.button(f"📍 {cc['source_ref']}", key="cc_ref"):
                    st.session_state["selected_ref"] = cc["source_ref"]

            # Urgency reasons
            reasons = slm.get("urgency_reasons", [])
            if reasons:
                with st.expander("Urgency reasons", expanded=True):
                    for r_idx, r in enumerate(reasons):
                        ref = r.get("source_ref", "")
                        st.markdown(f"- {html.escape(str(r.get('reason', '')))}  `{ref}`")
                        if st.button(f"📍 View source", key=f"reason_ref_{r_idx}_{ref}"):
                            st.session_state["selected_ref"] = ref

            # Rule hits
            non_negated = [rh for rh in rule_hits if not rh.get("negated")]
            if non_negated:
                with st.expander(f"⚠️ Rules fired ({len(non_negated)})"):
                    for rh in non_negated:
                        st.markdown(
                            f"- **[{rh['rule_id']}]** {html.escape(rh['label'])} "
                            f"→ floor `{rh['floor']}`  `{rh.get('source_ref', '')}`"
                        )

            # Symptom timeline
            timeline = slm.get("symptom_timeline", [])
            if timeline:
                st.subheader("Symptom Timeline")
                for s_idx, s in enumerate(timeline):
                    ref = s.get("source_ref", "")
                    quote = s.get("evidence_quote", "")
                    with st.expander(f"🔹 {html.escape(str(s.get('symptom', 'Symptom')))}"):
                        st.markdown(f"- **Onset:** {s.get('onset', 'UNKNOWN')}")
                        st.markdown(f"- **Duration:** {s.get('duration', 'UNKNOWN')}")
                        st.markdown(f"- **Severity:** {s.get('severity', 'UNKNOWN')}")
                        st.markdown(f"- **Progression:** {s.get('progression', 'UNKNOWN')}")
                        if quote:
                            st.markdown(f"- **Evidence:** *\"{html.escape(str(quote))}\"*")
                        if st.button(f"📍 Source: {ref}", key=f"sym_ref_{s_idx}_{ref}"):
                            st.session_state["selected_ref"] = ref

            # Report metrics
            metrics = slm.get("extracted_report_metrics", [])
            if metrics:
                st.subheader("Report Abnormalities")
                for m_idx, m in enumerate(metrics):
                    ref = m.get("source_ref", "")
                    status = m.get("status", "UNKNOWN")
                    status_color = "🔴" if status == "high" else "🔵" if status == "low" else "⚪"
                    st.markdown(
                        f"{status_color} **{html.escape(m.get('test_name', '?'))}**: "
                        f"{html.escape(m.get('value', '?'))} {html.escape(m.get('unit', ''))} "
                        f"(ref: {html.escape(m.get('reference_range', '?'))}, status: **{status}**)"
                    )
                    if st.button(f"📍 {ref}", key=f"metric_{m_idx}_{ref}"):
                        st.session_state["selected_ref"] = ref

            # Uncertainty flags
            u_flags = slm.get("uncertainty_flags", [])
            if u_flags:
                with st.expander(f"❓ Uncertainty flags ({len(u_flags)})"):
                    for uf in u_flags:
                        st.markdown(f"- **{uf.get('issue_type')}**: {html.escape(str(uf.get('reason', ''))[:100])}")

            # Missing information (produced by the SLM, previously not shown)
            missing_info = slm.get("missing_information", [])
            if missing_info:
                with st.expander(
                    f"🧩 Missing information ({len(missing_info)}) — ask before deciding",
                    expanded=True,
                ):
                    st.caption(
                        "The AI could not establish these facts from the intake. "
                        "They are not guesses — confirm them with the patient."
                    )
                    for item in missing_info:
                        st.markdown(f"- {html.escape(_pretty_missing_label(item))}")

            # Follow-up questions
            fup_qs = slm.get("follow_up_questions", [])
            if fup_qs:
                with st.expander("Follow-up questions"):
                    for q in fup_qs:
                        q_text = q.get("question", "") if isinstance(q, dict) else str(q)
                        st.markdown(f"- {html.escape(q_text)}")

            # Draft triage note
            note = slm.get("draft_triage_note", "")
            if note:
                with st.expander("📝 Draft triage note (AI suggestion, clinician must verify)", expanded=True):
                    st.text(note)

        # ---- Right column: Uploaded Files & Source Grounding ----
        with right:
            st.subheader("Patient Evidence & Files")
            tab_files, tab_grounding = st.tabs([
                "📁 Uploaded Files & Inputs",
                "🎯 Grounded Anchor"
            ])

            with tab_files:
                _render_all_uploaded_files(selected_id)

            with tab_grounding:
                selected_ref = st.session_state.get("selected_ref", "")
                if not selected_ref:
                    st.info("Click any 📍 button in the left panel to inspect the exact claim, sentence, or bounding box.")
                else:
                    st.markdown(f"**Anchor Reference:** `{selected_ref}`")
                    _render_source_grounding(selected_id, selected_ref, result)

        # ---- Sign-off panel ----
        st.divider()
        st.subheader("Clinician Sign-off")

        final_set = case.get("final_urgency")
        if final_set:
            st.success(f"✅ **Signed off.** Final urgency: **{URGENCY_LABELS.get(final_set, final_set)}**")
            actions = get_clinician_actions(selected_id)
            if actions:
                last = actions[-1]
                st.caption(f"Last action: {last['action']} by {last['actor']} at {last['created_at']}")
        else:
            ai_suggestion = URGENCY_LABELS.get(final_urgency, final_urgency)
            st.caption(f"AI suggestion: **{ai_suggestion}**  (Clinician must confirm or override)")

            if user_role == "doctor":
                with st.form("signoff_form"):
                    edited_note = st.text_area(
                        "Edit triage note (optional)",
                        value=slm.get("draft_triage_note", ""),
                        height=100,
                    )
                    col1, col2, col3 = st.columns(3)
                    with col1:
                        approve = st.form_submit_button("✅ Approve")
                    with col2:
                        edit_approve = st.form_submit_button("✏️ Edit & Approve")
                    with col3:
                        override_flag = st.form_submit_button("🔄 Override Urgency")

                if approve or edit_approve:
                    try:
                        record_clinician_action(
                            case_id=selected_id,
                            actor_id=user_id,
                            action="edit_approve" if edit_approve else "approve",
                            edited_note=edited_note if edit_approve else None,
                        )
                        audit.log_action(
                            actor_role="doctor",
                            action_type="clinician_action",
                            case_id=selected_id,
                            details={"action": "approve", "urgency": final_urgency},
                        )
                        st.success("Case signed off.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error: {e}")

                if override_flag:
                    st.session_state["override_mode"] = True

                if st.session_state.get("override_mode", False):
                    with st.form("override_form"):
                        new_urgency = st.selectbox(
                            "Override urgency to",
                            options=["LOW", "UNCERTAIN_NEEDS_FOLLOWUP", "MEDIUM", "HIGH", "CRITICAL"],
                        )
                        override_reason = st.text_area(
                            "Reason for override (required, min 10 chars)",
                            help="Clinical justification is required. This is enforced by the backend.",
                        )
                        override_submit = st.form_submit_button("Confirm Override")

                    if override_submit:
                        try:
                            record_clinician_action(
                                case_id=selected_id,
                                actor_id=user_id,
                                action="override",
                                new_urgency=new_urgency,
                                reason=override_reason,
                            )
                            audit.log_action(
                                actor_role="doctor",
                                action_type="override",
                                case_id=selected_id,
                                details={
                                    "urgency": new_urgency,
                                    "override_reason_len": len(override_reason),
                                },
                            )
                            st.session_state["override_mode"] = False
                            st.success(f"Urgency overridden to {new_urgency}.")
                            st.rerun()
                        except ValueError as e:
                            st.error(f"❌ {e}")
                        except Exception as e:
                            st.error(f"Error: {e}")

            else:
                st.info("👀 Read-only view. Only doctors can approve or override.")
                # Nurses can add a non-final note
                with st.form("nurse_note_form"):
                    nurse_note = st.text_area("Add note (non-final)")
                    if st.form_submit_button("Add note"):
                        try:
                            record_clinician_action(
                                case_id=selected_id,
                                actor_id=user_id,
                                action="note",
                                edited_note=nurse_note,
                            )
                            st.success("Note added.")
                        except Exception as e:
                            st.error(f"Error: {e}")

        # ---- Case audit trail (collapsible) ----
        import audit as _audit
        import hashlib
        import hmac
        from config import AUDIT_SALT
        case_hash = hmac.new(
            AUDIT_SALT.encode(), selected_id.encode(), hashlib.sha256
        ).hexdigest()

        audit_rows = _audit._db.get_audit_rows_for_case(case_hash)
        if audit_rows:
            with st.expander(f"📋 Case audit trail ({len(audit_rows)} entries)"):
                for row in audit_rows:
                    st.caption(
                        f"[{row['seq']}] {row['ts']} · **{row['action_type']}** "
                        f"by {row['actor_role']}"
                    )

        # Bottom back button
        st.divider()
        if st.button("← Back to Patient Queue", key="bottom_back_to_queue_btn", type="secondary"):
            st.session_state["clinician_view"] = "queue"
            st.session_state["selected_ref"] = ""
            st.rerun()


def _render_source_grounding(case_id: str, ref: str, result: dict):
    """Render the source content for a given ref."""
    from db import get_intakes

    channel = ref.split(":")[0] if ":" in ref else "text"

    if channel == "text":
        # Find the intake message
        intakes = get_intakes(case_id)
        matched_intake = None
        for intake in intakes:
            if intake["channel"] != "text":
                continue
            msg_ref = f"text:MSG_{intake['msg_index']}"
            if msg_ref == ref:
                matched_intake = intake
                break

        # Fallback to first text intake if ref is MSG_1 or msg_index wasn't explicitly set
        if not matched_intake:
            for intake in intakes:
                if intake["channel"] == "text":
                    matched_intake = intake
                    break

        if matched_intake:
            raw = matched_intake["content_text"] or ""
            question = (matched_intake.get("question") or "").strip()
            # Check for evidence_quote in slm output
            quote = ""
            slm = result.get("slm_output", {})
            for sym in slm.get("symptom_timeline", []):
                if sym.get("source_ref") == ref and sym.get("evidence_quote"):
                    quote = sym["evidence_quote"]
                    break

            if question:
                st.markdown("**Follow-up question:**")
                st.markdown(
                    f'<div style="background:#eef2ff;color:#1e293b;border:1px solid #c7d2fe;'
                    f'padding:10px 12px;border-radius:6px;font-size:0.9rem;line-height:1.4;margin-bottom:8px;">'
                    f'{html.escape(question)}</div>',
                    unsafe_allow_html=True,
                )
                st.markdown("**Patient answer:**")
            else:
                st.markdown("**Patient text:**")
            highlighted = _highlight_text(raw, quote)
            st.markdown(
                f'<div style="background:#f8fafc;color:#0f172a;border:1px solid #cbd5e1;'
                f'padding:12px;border-radius:6px;white-space:pre-wrap;font-size:0.95rem;line-height:1.5;">'
                f'{highlighted}</div>',
                unsafe_allow_html=True,
            )
            return

        st.warning(f"Text source `{ref}` not found in intakes.")

    elif channel == "voice":
        # Show audio player + transcript
        intakes = get_intakes(case_id)
        for intake in intakes:
            if intake["channel"] == "voice":
                audio_path = resolve_intake_path(intake["content_ref"] or "", case_id)
                if audio_path and os.path.exists(audio_path):
                    # Parse start time from ref voice:MM:SS-MM:SS
                    start_s = 0.0
                    try:
                        time_part = ref.split(":")[1]  # e.g. "00:30-01:15"
                        start_part = time_part.split("-")[0]
                        mm, ss = start_part.split(":")
                        start_s = int(mm) * 60 + int(ss)
                    except Exception:
                        pass

                    st.markdown("**Voice recording:**")
                    with open(audio_path, "rb") as af:
                        st.audio(af.read(), start_time=int(start_s))

                    # Live recordings cache <audio>.json; demo cases use
                    # stt.json in the same folder. Accept both.
                    audio_stem = os.path.splitext(audio_path)[0]
                    stt_path = next(
                        (
                            p for p in (
                                audio_stem + ".json",
                                os.path.join(os.path.dirname(audio_path), "stt.json"),
                            )
                            if os.path.exists(p)
                        ),
                        None,
                    )
                    if stt_path:
                        with open(stt_path, encoding="utf-8") as f:
                            stt = json.load(f)
                        st.markdown("**Transcript:**")
                        for seg in stt.get("segments", []):
                            low = seg.get("low_confidence", False)
                            seg_text = html.escape(seg.get("text", ""))
                            if low:
                                seg_text = f'<span style="background:#fee2e2;color:#991b1b;padding:2px 6px;border-radius:3px;font-weight:600;">{seg_text}</span>'
                            st.markdown(seg_text, unsafe_allow_html=True)
                        if not stt.get("segments") and stt.get("text"):
                            st.markdown(html.escape(stt["text"]))
                    else:
                        st.info("Transcript sidecar not found for this recording.")
                    return
        st.warning("Audio file not found.")

    elif channel == "ocr":
        # Show report image with bbox drawn
        intakes = get_intakes(case_id)
        for intake in intakes:
            if intake["channel"] == "ocr":
                report_path = resolve_intake_path(intake["content_ref"] or "", case_id)
                ocr_data = _load_report_ocr(
                    report_path, intake["ocr_line_start"] or 1, case_id
                )

                if ocr_data and ocr_data.get("lines"):
                    # Find the selected line
                    selected_line = None
                    for line in ocr_data.get("lines", []):
                        if line["line_id"] == ref:
                            selected_line = line
                            break

                    # Precomputed OCR JSON may contain an absolute path from the
                    # machine that generated it (e.g. a Windows path). Fall back
                    # to the report file that is actually stored on disk.
                    image_path = ocr_data.get("image_path") or report_path
                    if not os.path.exists(image_path):
                        image_path = report_path
                    if os.path.exists(image_path):
                        if selected_line and selected_line.get("bbox"):
                            unreadable = selected_line.get("unreadable", False)
                            color = "red" if unreadable else "blue"
                            img_bytes = _draw_bbox_on_image(
                                image_path,
                                selected_line["bbox"],
                                color=color,
                                label=selected_line["line_id"],
                            )
                            if img_bytes:
                                st.image(img_bytes, caption=f"Report - {ref} highlighted", width="stretch")
                                if unreadable:
                                    st.error(f"⚠️ This region is unreadable (confidence: {selected_line.get('confidence', '?'):.0%})")
                                else:
                                    st.markdown(f"**Line text:** {html.escape(selected_line.get('text', ''))}")
                        else:
                            st.image(image_path, caption="Report image", width="stretch")
                    return
        st.warning("Report file not found.")

    elif channel == "vitals":
        # Show vitals from the intakes
        intakes = get_intakes(case_id)
        for intake in intakes:
            if intake["channel"] == "vitals":
                try:
                    v = json.loads(intake["content_text"] or "{}")
                    st.markdown("**Vitals:**")
                    for k, val in v.items():
                        st.metric(k, val)
                    return
                except Exception:
                    pass
        st.warning("Vitals not found.")
