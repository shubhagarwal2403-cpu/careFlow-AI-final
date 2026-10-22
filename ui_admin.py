"""
ui_admin.py — CareFlow AI Admin panel
Audit log viewer, chain verification, erasure records,
performance widget, demo loader, config view.
"""

import streamlit as st
import json
import os

from db import list_cases_for_queue, DB_PATH, init_db, get_conn
import audit


def render_admin_page():
    """Admin panel — no clinical content visible to admin."""
    st.title("🔧 Admin Panel")
    user_role = st.session_state.get("user_role", "")

    if user_role != "admin":
        st.error("Access denied. Admin only.")
        return

    st.warning(
        "Admins see only metadata — no clinical content, no patient text, no transcripts."
    )

    tab_audit, tab_perf, tab_demo, tab_config = st.tabs([
        "📋 Audit Log", "📊 Performance", "🔁 Demo Cases", "⚙️ Config"
    ])

    # ================================================================
    # TAB 1: Audit log
    # ================================================================
    with tab_audit:
        st.subheader("Audit Log")

        # Chain verification
        result = audit.verify()
        if result["ok"]:
            st.success(f"✅ Chain verified — {result['checked']} entries checked, all intact.")
        else:
            st.error(
                f"❌ Chain BROKEN at seq {result['first_bad_seq']}! "
                "The audit log may have been tampered with."
            )

        rows = audit._db.get_all_audit_rows()
        if not rows:
            st.info("No audit entries yet.")
        else:
            # Show as table (no PHI fields)
            display_rows = []
            for r in rows:
                display_rows.append({
                    "seq": r["seq"],
                    "ts": r["ts"],
                    "actor_role": r["actor_role"],
                    "action_type": r["action_type"],
                    "case_hash": r["case_hash"][:16] + "..." if r["case_hash"] else "-",
                    "confidence": r["confidence"],
                })
            st.dataframe(display_rows, use_container_width=True)

            # Erasure records filter
            erasure_rows = [r for r in rows if r["action_type"] == "erasure"]
            if erasure_rows:
                st.subheader(f"Erasure Records ({len(erasure_rows)})")
                for r in erasure_rows:
                    st.caption(
                        f"seq={r['seq']} · {r['ts']} · "
                        f"case_hash={r['case_hash'][:16] if r['case_hash'] else '-'}... "
                        f"(no clinical content retained)"
                    )

    # ================================================================
    # TAB 2: Performance
    # ================================================================
    with tab_perf:
        st.subheader("Performance Widget")
        st.caption("Latency and token statistics from actual API calls only. Never placeholders.")

        from db import get_conn
        with get_conn() as conn:
            rows = conn.execute(
                """SELECT latency_ms, tokens_in, tokens_out, model, cached
                   FROM ai_outputs WHERE cached = 0 ORDER BY created_at""",
            ).fetchall()
            rows = [dict(r) for r in rows]

        latency_json_path = os.path.join(os.path.dirname(__file__), "docs", "latency.json")
        latency_data = {}
        if os.path.exists(latency_json_path):
            with open(latency_json_path) as f:
                latency_data = json.load(f)

        def _has_number(key: str) -> bool:
            return isinstance(latency_data.get(key), (int, float))

        latency_ready = _has_number("p50_ms") and _has_number("p95_ms")
        if not rows and not latency_ready:
            st.info("⏱️ Not yet measured — run `python -m scripts.bench` to populate latency data.")
        else:
            col1, col2, col3 = st.columns(3)

            if latency_ready:
                with col1:
                    st.metric("p50 latency", f"{latency_data['p50_ms']:.0f} ms")
                with col2:
                    st.metric("p95 latency", f"{latency_data['p95_ms']:.0f} ms")
                with col3:
                    avg_out = latency_data.get('avg_tokens_out')
                    st.metric("avg tokens out", f"{avg_out:.0f}" if isinstance(avg_out, (int, float)) else "N/A")
                st.caption(
                    f"Model: {latency_data.get('model', 'N/A')} · "
                    f"n={latency_data.get('n_samples', 0)} samples · "
                    f"Source: docs/latency.json from bench.py"
                )
            elif rows:
                latencies = sorted([r["latency_ms"] for r in rows if r["latency_ms"]])
                if latencies:
                    n = len(latencies)
                    p50 = latencies[int(n * 0.5)]
                    p95 = latencies[int(n * 0.95)]
                    avg_tok_out = sum(r["tokens_out"] or 0 for r in rows) / n
                    with col1:
                        st.metric("p50 latency", f"{p50:.0f} ms")
                    with col2:
                        st.metric("p95 latency", f"{p95:.0f} ms")
                    with col3:
                        st.metric("avg tokens out", f"{avg_tok_out:.0f}")
                    st.caption(f"n={n} live calls from db")

        # AI mode switcher
        st.subheader("AI Mode")
        from config import CAREFLOW_AI_MODE
        current_mode = st.session_state.get("ai_mode_override", CAREFLOW_AI_MODE)
        new_mode = st.radio(
            "AI mode",
            options=["auto", "live", "cached"],
            index=["auto", "live", "cached"].index(current_mode),
            horizontal=True,
            key="admin_ai_mode",
            help=(
                "auto: try the live SLM, then cached demo output. "
                "live: API only. cached: no network — best for the demo."
            ),
        )
        if new_mode != current_mode:
            st.session_state["ai_mode_override"] = new_mode
            st.success(f"AI mode set to **{new_mode}** for this session.")

    # ================================================================
    # TAB 3: Demo Cases
    # ================================================================
    with tab_demo:
        st.subheader("Demo Case Loader")

        col1, col2 = st.columns(2)
        with col1:
            if st.button("📥 Load demo cases", type="primary"):
                with st.spinner("Loading demo cases..."):
                    try:
                        import subprocess
                        import sys
                        result = subprocess.run(
                            [sys.executable, "-m", "scripts.load_demo_cases"],
                            capture_output=True,
                            text=True,
                            cwd=os.path.dirname(__file__),
                        )
                        if result.returncode == 0:
                            st.success("Demo cases loaded successfully!")
                            st.text(result.stdout[-500:] if result.stdout else "")
                        else:
                            st.error(f"Error: {result.stderr[-300:]}")
                    except Exception as e:
                        st.error(f"Error: {e}")

        with col2:
            if st.button("🔄 Reset demo (clear all cases)", type="secondary"):
                st.session_state["confirm_reset"] = True

        if st.session_state.get("confirm_reset", False):
            st.warning("This will delete ALL cases from the database.")
            c1, c2 = st.columns(2)
            with c1:
                if st.button("Yes, reset", type="primary"):
                    with get_conn() as conn:
                        conn.execute("DELETE FROM clinician_actions")
                        conn.execute("DELETE FROM ai_outputs")
                        conn.execute("DELETE FROM intakes")
                        conn.execute("DELETE FROM cases")
                    # Remove uploaded/derived media too; otherwise report images
                    # and voice notes would remain on disk after a reset.
                    import shutil
                    uploads_dir = os.path.join(os.path.dirname(__file__), "data", "uploads")
                    shutil.rmtree(uploads_dir, ignore_errors=True)
                    os.makedirs(uploads_dir, exist_ok=True)
                    for key in ("selected_case_id", "current_case_id", "pipeline_result"):
                        st.session_state.pop(key, None)
                    st.session_state["clinician_view"] = "queue"
                    st.session_state["confirm_reset"] = False
                    st.success("Reset complete.")
                    st.rerun()
            with c2:
                if st.button("Cancel"):
                    st.session_state["confirm_reset"] = False
                    st.rerun()

        # Show current cases summary
        cases = list_cases_for_queue()
        if cases:
            st.caption(f"{len(cases)} case(s) in database")

    # ================================================================
    # TAB 4: Config view (read-only)
    # ================================================================
    with tab_config:
        st.subheader("Configuration (read-only)")

        import config as cfg
        st.markdown("**Models**")
        st.code(
            f"SLM_MODEL = {cfg.SLM_MODEL}\n"
            f"STT_MODEL = {cfg.STT_MODEL}\n"
            f"OCR_PROVIDER = {cfg.OCR_PROVIDER}\n"
            f"VISION_MODEL = {cfg.VISION_MODEL}\n"
            f"TESSERACT_LANG = {cfg.TESSERACT_LANG}"
        )
        st.caption(
            "Report OCR order: precomputed demo JSON → Groq vision model → "
            "Tesseract (optional offline fallback). JSON is only used for benchmark cases."
        )

        st.markdown("**Urgency ordering**")
        st.code(f"MERGE ORDER: {cfg.URGENCY_MERGE_ORDER}\nQUEUE ORDER: {cfg.URGENCY_QUEUE_ORDER}")

        st.markdown("**Thresholds (demo assumptions, not clinically validated)**")
        st.json({
            "STT_LOW_CONF": cfg.STT_LOW_CONF,
            "OCR_CONF_MIN": cfg.OCR_CONF_MIN,
            "SLM_TEMPERATURE": cfg.SLM_TEMPERATURE,
            "OVERRIDE_REASON_MIN_LEN": cfg.OVERRIDE_REASON_MIN_LEN,
            "UNCERTAIN_ESCALATE_MIN": cfg.UNCERTAIN_ESCALATE_MIN,
        })

        st.markdown("**Rules**")
        for rule in cfg.RULES_TABLE:
            st.markdown(f"- [{rule['rule_id']}] **{rule['label']}** → `{rule['floor']}`")
