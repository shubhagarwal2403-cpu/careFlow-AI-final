"""
app.py — CareFlow AI Streamlit entry point
Login page, sidebar navigation, persistent banner.
All users must login first (patients, nurses, doctors, admins).
Nurses can do intake on behalf of patients who can't.
"""

import streamlit as st
import html
import os
import sys

# Add careflow directory to path
sys.path.insert(0, os.path.dirname(__file__))

from db import init_db, upsert_user, DB_PATH, authenticate_user, register_user, get_user

# Initialise the database on startup
init_db()

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="CareFlow AI",
    page_icon="🏥",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Custom CSS for premium UI
# ---------------------------------------------------------------------------
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');

    /* Global typography */
    html, body, [class*="css"] {
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    }

    /* Login card styling */
    .login-container {
        max-width: 480px;
        margin: 0 auto;
        padding: 48px 40px;
        background: linear-gradient(135deg, rgba(30,41,59,0.95) 0%, rgba(15,23,42,0.98) 100%);
        border: 1px solid rgba(99,102,241,0.2);
        border-radius: 20px;
        backdrop-filter: blur(20px);
        box-shadow: 0 25px 60px rgba(0,0,0,0.4), 0 0 40px rgba(99,102,241,0.1);
    }
    .login-title {
        text-align: center;
        font-size: 2.2rem;
        font-weight: 700;
        margin-bottom: 6px;
        background: linear-gradient(135deg, #818cf8, #6366f1, #4f46e5);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        letter-spacing: -0.02em;
    }
    .login-subtitle {
        text-align: center;
        font-size: 0.9rem;
        color: #94a3b8;
        margin-bottom: 32px;
        font-weight: 400;
    }

    /* Role cards */
    .role-grid {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 12px;
        margin: 16px 0 24px 0;
    }
    .role-card {
        padding: 20px 16px;
        border-radius: 14px;
        border: 1px solid rgba(99,102,241,0.15);
        text-align: center;
        cursor: pointer;
        transition: all 0.25s ease;
        background: rgba(30,41,59,0.6);
    }
    .role-card:hover {
        border-color: rgba(99,102,241,0.5);
        background: rgba(99,102,241,0.08);
        transform: translateY(-2px);
        box-shadow: 0 8px 25px rgba(99,102,241,0.15);
    }
    .role-icon {
        font-size: 2rem;
        margin-bottom: 8px;
        display: block;
    }
    .role-name {
        font-weight: 600;
        font-size: 0.95rem;
        color: #e2e8f0;
    }
    .role-desc {
        font-size: 0.75rem;
        color: #64748b;
        margin-top: 4px;
    }

    /* Sidebar improvements */
    section[data-testid="stSidebar"] {
        background: linear-gradient(180deg, #0f172a 0%, #1e293b 100%);
        border-right: 1px solid rgba(99,102,241,0.1);
    }
    section[data-testid="stSidebar"] .stTitle {
        font-size: 1.4rem;
        letter-spacing: -0.01em;
    }

    /* Badge styling */
    .urgency-badge {
        display: inline-block;
        padding: 4px 14px;
        border-radius: 20px;
        font-size: 0.8rem;
        font-weight: 600;
        letter-spacing: 0.02em;
    }

    /* Metric cards */
    [data-testid="stMetric"] {
        background: rgba(30,41,59,0.7);
        border: 1px solid rgba(99,102,241,0.12);
        border-radius: 12px;
        padding: 16px;
    }

    /* Form styling */
    .stForm {
        border: 1px solid rgba(99,102,241,0.15) !important;
        border-radius: 14px !important;
        padding: 24px !important;
        background: rgba(30,41,59,0.4) !important;
    }

    /* Button improvements */
    .stButton > button[kind="primary"] {
        background: linear-gradient(135deg, #6366f1, #4f46e5) !important;
        border: none !important;
        border-radius: 10px !important;
        font-weight: 600 !important;
        padding: 10px 24px !important;
        transition: all 0.2s ease !important;
    }
    .stButton > button[kind="primary"]:hover {
        box-shadow: 0 4px 20px rgba(99,102,241,0.4) !important;
        transform: translateY(-1px) !important;
    }
    .stButton > button[kind="secondary"] {
        border-radius: 10px !important;
        border: 1px solid rgba(99,102,241,0.3) !important;
        font-weight: 500 !important;
        transition: all 0.2s ease !important;
    }

    /* Expander styling */
    .streamlit-expanderHeader {
        border-radius: 10px !important;
        font-weight: 500 !important;
    }

    /* Tab styling */
    .stTabs [data-baseweb="tab-list"] {
        gap: 4px;
    }
    .stTabs [data-baseweb="tab"] {
        border-radius: 8px 8px 0 0;
        padding: 10px 20px;
        font-weight: 500;
    }

    /* Divider */
    hr {
        border-color: rgba(99,102,241,0.1) !important;
    }

    /* Banner */
    .prototype-banner {
        background: linear-gradient(135deg, rgba(251,191,36,0.1) 0%, rgba(251,191,36,0.05) 100%);
        color: #fbbf24;
        border: 1px solid rgba(251,191,36,0.2);
        border-radius: 10px;
        padding: 10px 18px;
        margin-bottom: 8px;
        font-size: 0.82rem;
        font-weight: 500;
        letter-spacing: 0.01em;
    }

    /* Logged in user display */
    .user-chip {
        display: inline-flex;
        align-items: center;
        gap: 8px;
        padding: 8px 16px;
        background: linear-gradient(135deg, rgba(99,102,241,0.15) 0%, rgba(99,102,241,0.05) 100%);
        border: 1px solid rgba(99,102,241,0.25);
        border-radius: 10px;
        font-size: 0.85rem;
        font-weight: 500;
        color: #c7d2fe;
    }
    .user-chip .user-role {
        font-size: 0.7rem;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        color: #818cf8;
        font-weight: 600;
    }

    /* Nurse on-behalf badge */
    .on-behalf-badge {
        display: inline-block;
        padding: 4px 12px;
        background: rgba(234,179,8,0.15);
        border: 1px solid rgba(234,179,8,0.3);
        border-radius: 8px;
        font-size: 0.75rem;
        color: #eab308;
        font-weight: 500;
    }

    /* Accessibility + polish */
    button:focus-visible, [role="button"]:focus-visible,
    input:focus-visible, textarea:focus-visible, [role="tab"]:focus-visible {
        outline: 2px solid #818cf8 !important;
        outline-offset: 2px !important;
    }
    [data-testid="stImage"] img {
        border-radius: 10px;
        border: 1px solid rgba(99,102,241,0.22);
    }
    [data-testid="stDataFrame"] {
        border: 1px solid rgba(99,102,241,0.15);
        border-radius: 10px;
        overflow: hidden;
    }
    code {
        background: rgba(99,102,241,0.12) !important;
        color: #c7d2fe !important;
        border-radius: 5px;
        padding: 1px 5px;
    }
    ::-webkit-scrollbar { width: 10px; height: 10px; }
    ::-webkit-scrollbar-thumb {
        background: rgba(99,102,241,0.35);
        border-radius: 10px;
    }
    ::-webkit-scrollbar-track { background: rgba(15,23,42,0.35); }

    @media (max-width: 768px) {
        .login-container { padding: 30px 20px; }
        .login-title { font-size: 1.8rem; }
        .role-grid { grid-template-columns: 1fr; }
    }
    @media (prefers-reduced-motion: reduce) {
        * { transition: none !important; animation: none !important; }
    }
    @media print {
        section[data-testid="stSidebar"], [data-testid="stToolbar"],
        .prototype-banner, .stButton, [data-testid="stFileUploader"] { display: none !important; }
        .main .block-container { max-width: 100% !important; }
    }
</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Login page
# ---------------------------------------------------------------------------
def render_login_page():
    """Full-screen authentication page with real Email/Password login, registration, and 1-click demo shortcuts."""
    st.markdown("<br>", unsafe_allow_html=True)

    col_l, col_c, col_r = st.columns([1, 2.2, 1])
    with col_c:
        st.markdown("""
        <div style="text-align:center; margin-bottom: 28px;">
            <div style="font-size: 3.2rem; margin-bottom: 4px;">🏥</div>
            <div class="login-title">CareFlow AI</div>
            <div class="login-subtitle">Intelligent Clinical Intake & Triage Decision Support</div>
        </div>
        """, unsafe_allow_html=True)

        tab_login, tab_register, tab_quick = st.tabs([
            "🔐 Sign In", 
            "📝 Register Account",
            "⚡ Instant Demo Access"
        ])

        with tab_login:
            st.markdown("<p style='color:#94a3b8; font-size:0.85rem;'>Sign in with your registered email and password.</p>", unsafe_allow_html=True)
            with st.form("login_form", clear_on_submit=False):
                email = st.text_input("Email address", placeholder="e.g. doctor@hospital.org", key="login_email")
                password = st.text_input("Password", type="password", placeholder="••••••••", key="login_password")
                
                submitted = st.form_submit_button("Sign In with Email", type="primary", use_container_width=True)
                if submitted:
                    if not email.strip() or not password:
                        st.error("Please enter both email and password.")
                    else:
                        user = authenticate_user(email.strip(), password)
                        if user:
                            st.session_state["logged_in"] = True
                            st.session_state["user_id"] = user["id"]
                            st.session_state["user_name"] = user["name"]
                            st.session_state["user_email"] = user["email"]
                            st.session_state["user_role"] = user["role"]
                            st.session_state["nurse_on_behalf"] = (user["role"] == "nurse")
                            st.session_state["nurse_on_behalf_toggle"] = (user["role"] == "nurse")
                            st.success(f"Welcome back, {user['name']}!")
                            st.rerun()
                        else:
                            st.error("Invalid email or password. Please verify your credentials.")

        with tab_register:
            st.markdown("<p style='color:#94a3b8; font-size:0.85rem;'>Create a persistent account stored in the clinical database.</p>", unsafe_allow_html=True)
            with st.form("register_form", clear_on_submit=False):
                reg_name = st.text_input("Full Name", placeholder="e.g. Dr. Ananya Sharma or Ramesh Gupta", key="reg_name")
                reg_email = st.text_input("Email address", placeholder="e.g. user@careflow.ai", key="reg_email")
                reg_pwd = st.text_input("Password (min 6 characters)", type="password", placeholder="••••••••", key="reg_pwd")
                reg_role = st.selectbox(
                    "Role / Account Type",
                    options=["patient", "nurse", "doctor", "admin"],
                    format_func=lambda r: {
                        "patient": "👤 Patient — Self-service intake & health reporting",
                        "nurse": "🩺 Nurse — Intake & triage queue management",
                        "doctor": "👨‍⚕️ Doctor — Clinical review, sign-off & overrides",
                        "admin": "🔧 Admin — Compliance audit, metrics & configuration",
                    }[r],
                    key="reg_role",
                )
                
                reg_submit = st.form_submit_button("Create Account & Sign In", type="primary", use_container_width=True)
                if reg_submit:
                    if not reg_name.strip() or not reg_email.strip() or not reg_pwd:
                        st.error("Please fill in all fields.")
                    elif len(reg_pwd) < 6:
                        st.error("Password must be at least 6 characters.")
                    else:
                        try:
                            new_user = register_user(reg_name.strip(), reg_email.strip(), reg_pwd, reg_role)
                            st.session_state["logged_in"] = True
                            st.session_state["user_id"] = new_user["id"]
                            st.session_state["user_name"] = new_user["name"]
                            st.session_state["user_email"] = new_user["email"]
                            st.session_state["user_role"] = new_user["role"]
                            st.session_state["nurse_on_behalf"] = (new_user["role"] == "nurse")
                            st.session_state["nurse_on_behalf_toggle"] = (new_user["role"] == "nurse")
                            st.success(f"Account created successfully! Welcome, {new_user['name']}.")
                            st.rerun()
                        except Exception as e:
                            st.error(str(e))

        with tab_quick:
            st.markdown("<p style='color:#94a3b8; font-size:0.85rem;'>1-click presentation shortcuts with pre-seeded test accounts:</p>", unsafe_allow_html=True)
            qc1, qc2 = st.columns(2)
            with qc1:
                if st.button("👨‍⚕️ Dr. Sharma  ·  Doctor", use_container_width=True, key="quick_doc"):
                    u = authenticate_user("doctor@hospital.org", "doctor123")
                    if u:
                        st.session_state["logged_in"] = True
                        st.session_state["user_id"] = u["id"]
                        st.session_state["user_name"] = u["name"]
                        st.session_state["user_email"] = u["email"]
                        st.session_state["user_role"] = u["role"]
                        st.session_state["nurse_on_behalf"] = False
                        st.session_state["nurse_on_behalf_toggle"] = False
                        st.rerun()
                
                if st.button("👤 Priya Patel  ·  Patient", use_container_width=True, key="quick_pat"):
                    u = authenticate_user("patient@careflow.ai", "patient123")
                    if u:
                        st.session_state["logged_in"] = True
                        st.session_state["user_id"] = u["id"]
                        st.session_state["user_name"] = u["name"]
                        st.session_state["user_email"] = u["email"]
                        st.session_state["user_role"] = u["role"]
                        st.session_state["nurse_on_behalf"] = False
                        st.session_state["nurse_on_behalf_toggle"] = False
                        st.rerun()

            with qc2:
                if st.button("🩺 Sunita Sharma  ·  Nurse", use_container_width=True, key="quick_nurse"):
                    u = authenticate_user("nurse@hospital.org", "nurse123")
                    if u:
                        st.session_state["logged_in"] = True
                        st.session_state["user_id"] = u["id"]
                        st.session_state["user_name"] = u["name"]
                        st.session_state["user_email"] = u["email"]
                        st.session_state["user_role"] = u["role"]
                        st.session_state["nurse_on_behalf"] = True
                        st.session_state["nurse_on_behalf_toggle"] = True
                        st.rerun()

                if st.button("🔧 Hospital Administrator", use_container_width=True, key="quick_admin"):
                    u = authenticate_user("admin@hospital.org", "admin123")
                    if u:
                        st.session_state["logged_in"] = True
                        st.session_state["user_id"] = u["id"]
                        st.session_state["user_name"] = u["name"]
                        st.session_state["user_email"] = u["email"]
                        st.session_state["user_role"] = u["role"]
                        st.session_state["nurse_on_behalf"] = False
                        st.session_state["nurse_on_behalf_toggle"] = False
                        st.rerun()

            st.caption("Credentials: `doctor@hospital.org` / `doctor123` · `nurse@hospital.org` / `nurse123`")

        # Footer
        st.markdown("""
        <div style="text-align:center; margin-top: 36px; padding-top: 20px;
                    border-top: 1px solid rgba(99,102,241,0.1);">
            <span style="font-size: 0.72rem; color: #64748b; font-weight: 400;">
                🔒 Secure Authentication · SHA-256 PBKDF2 Password Hashing · Role-Based Access Control<br>
                ⚠️ Prototype for demonstration · Synthetic data only · Not a medical device
            </span>
        </div>
        """, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Check login state
# ---------------------------------------------------------------------------
if not st.session_state.get("logged_in", False):
    render_login_page()
    st.stop()


# ---------------------------------------------------------------------------
# Persistent banner
# ---------------------------------------------------------------------------
st.markdown(
    """
    <div class="prototype-banner">
    ⚠️ <strong>Prototype. Synthetic data only. Not a medical device.
    Triage levels are demo assumptions — not clinically validated.</strong>
    </div>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Sidebar: User info + page selector
# ---------------------------------------------------------------------------
user_id = st.session_state.get("user_id", "")
user_name = st.session_state.get("user_name", "User")
user_email = st.session_state.get("user_email", "")
role = st.session_state.get("user_role", "patient")

with st.sidebar:
    st.markdown("""
    <div style="text-align:center; margin-bottom: 16px;">
        <span style="font-size: 2rem;">🏥</span><br>
        <span style="font-size: 1.3rem; font-weight: 700;
              background: linear-gradient(135deg, #818cf8, #6366f1);
              -webkit-background-clip: text; -webkit-text-fill-color: transparent;">
            CareFlow AI
        </span>
    </div>
    """, unsafe_allow_html=True)

    st.caption("Clinical intake triage — prototype")

    st.divider()

    # User chip
    role_icons = {"patient": "👤", "nurse": "🩺", "doctor": "👨‍⚕️", "admin": "🔧"}
    icon = role_icons.get(role, "👤")
    st.markdown(
        f"""<div class="user-chip">
            <span style="font-size:1.4rem">{icon}</span>
            <div>
                <div style="font-weight:600;color:#e2e8f0;font-size:0.95rem;">{html.escape(str(user_name))}</div>
                <div class="user-role" style="font-size:0.75rem;text-transform:uppercase;color:#818cf8;font-weight:600;">{html.escape(str(role))}</div>
                {f'<div style="font-size:0.72rem;color:#64748b;">{html.escape(str(user_email))}</div>' if user_email else ''}
            </div>
        </div>""",
        unsafe_allow_html=True,
    )

    # Nurse on-behalf toggle
    if role == "nurse":
        st.markdown("<br>", unsafe_allow_html=True)
        # The patient page can request re-activation. Apply it before the
        # checkbox widget is created; writing to its key after creation raises
        # StreamlitWidgetAlreadyInstantiatedError.
        if st.session_state.pop("_activate_nurse_mode", False):
            st.session_state["nurse_on_behalf"] = True
            st.session_state["nurse_on_behalf_toggle"] = True
        if "nurse_on_behalf" not in st.session_state:
            st.session_state["nurse_on_behalf"] = True
        if "nurse_on_behalf_toggle" not in st.session_state:
            st.session_state["nurse_on_behalf_toggle"] = st.session_state["nurse_on_behalf"]

        def _sync_nurse_toggle():
            st.session_state["nurse_on_behalf"] = st.session_state["nurse_on_behalf_toggle"]

        # Do not pass value= here: the key already exists in session_state.
        # Passing both raises Streamlit's "default value + Session State API"
        # warning and can desync the toggle from the stored mode.
        on_behalf = st.checkbox(
            "👥 Nurse-assisted intake mode",
            help="Enable if the patient cannot fill the form themselves (e.g. unconscious, elderly, child). You will record proxy consent.",
            key="nurse_on_behalf_toggle",
            on_change=_sync_nurse_toggle,
        )
        st.session_state["nurse_on_behalf"] = on_behalf
        if on_behalf:
            st.markdown(
                '<div class="on-behalf-badge" style="margin-top:6px">👥 Assisting patient who cannot self-report</div>',
                unsafe_allow_html=True,
            )
    else:
        on_behalf = False
        st.session_state["nurse_on_behalf"] = False
        st.session_state["nurse_on_behalf_toggle"] = False

    st.divider()

    # Page selector (role-gated visibility is cosmetic; backend enforces)
    if role == "patient":
        pages = ["Patient Intake"]
    elif role == "nurse":
        pages = ["Patient Intake", "Clinician Queue"]
    elif role == "doctor":
        pages = ["Clinician Queue"]
    elif role == "admin":
        pages = ["Admin Panel"]
    else:
        pages = ["Patient Intake"]

    # Cross-page navigation requested during the previous run (e.g. "View
    # Case in Clinical Queue"). Apply it before the radio widget exists.
    pending_page = st.session_state.pop("_pending_page", None)
    if pending_page in pages:
        st.session_state["current_page"] = pending_page

    page = st.radio("Navigate to", pages, key="current_page")

    st.divider()

    # Logout button
    if st.button("🚪 Log Out", use_container_width=True, type="secondary"):
        for key in list(st.session_state.keys()):
            del st.session_state[key]
        st.rerun()

# ---------------------------------------------------------------------------
# Route to page
# ---------------------------------------------------------------------------
if page == "Patient Intake":
    from ui_patient import render_patient_page
    render_patient_page()

elif page == "Clinician Queue":
    from ui_clinician import render_clinician_page
    render_clinician_page()

elif page == "Admin Panel":
    from ui_admin import render_admin_page
    render_admin_page()
