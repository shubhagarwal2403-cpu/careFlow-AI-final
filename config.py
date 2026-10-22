"""
config.py — CareFlow AI configuration
All thresholds, enums, model names, rule tables, and deny-lists.
Change values here; no magic numbers elsewhere.
"""

import os
from dotenv import load_dotenv

_base_dir = os.path.dirname(os.path.abspath(__file__))
for _env_file in [
    os.path.join(_base_dir, ".env"),
    os.path.join(os.path.dirname(_base_dir), ".env"),
    os.path.join(_base_dir, ".env.example"),
]:
    if os.path.exists(_env_file):
        load_dotenv(_env_file)
        if os.getenv("GROQ_API_KEY") and "your_groq_api_key" not in os.getenv("GROQ_API_KEY", ""):
            break
else:
    load_dotenv()

# ---------------------------------------------------------------------------
# Urgency ordering (two orderings serve different purposes)
# ---------------------------------------------------------------------------
# MERGE_ORDER: used by merge_urgency() — the higher index wins.
URGENCY_MERGE_ORDER = [
    "LOW",
    "UNCERTAIN_NEEDS_FOLLOWUP",
    "MEDIUM",
    "HIGH",
    "CRITICAL",
]

# QUEUE_ORDER: display order in the clinician queue (most urgent first).
URGENCY_QUEUE_ORDER = [
    "CRITICAL",
    "HIGH",
    "MEDIUM",
    "LOW",
    "UNCERTAIN_NEEDS_FOLLOWUP",
]

URGENCY_LEVELS = set(URGENCY_MERGE_ORDER)

# Minimum urgency when injection is detected (D6)
INJECTION_URGENCY_FLOOR = "UNCERTAIN_NEEDS_FOLLOWUP"

# ---------------------------------------------------------------------------
# Model / API config (from .env)
# ---------------------------------------------------------------------------
GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
SLM_MODEL: str = os.getenv("SLM_MODEL", "openai/gpt-oss-20b")
STT_MODEL: str = os.getenv("STT_MODEL", "whisper-large-v3-turbo")
STT_PROVIDER: str = os.getenv("STT_PROVIDER", "groq")  # groq | precomputed | bhashini

# OCR / report vision
# auto: try the Groq vision model first, then fall back to Tesseract.
# vision: Groq multimodal model only.
# tesseract: fully local OCR only.
# precomputed: read ocr.json shipped with the demo cases.
OCR_PROVIDER: str = os.getenv("OCR_PROVIDER", "auto")
VISION_MODEL: str = os.getenv("VISION_MODEL", "qwen/qwen3.8-27b")
VISION_TIMEOUT_S: int = int(os.getenv("VISION_TIMEOUT_S", "60"))
VISION_MAX_IMAGE_DIM: int = int(os.getenv("VISION_MAX_IMAGE_DIM", "1600"))
TESSERACT_CMD: str = os.getenv("TESSERACT_CMD", "")  # path to tesseract exe, or ""
TESSERACT_LANG: str = os.getenv("TESSERACT_LANG", "eng")  # e.g. "eng" or "eng+hin"
CAREFLOW_AI_MODE: str = os.getenv("CAREFLOW_AI_MODE", "auto")  # live|cached|auto
AUDIT_SALT: str = os.getenv("AUDIT_SALT", "CHANGE_ME")

# SLM call settings
SLM_TEMPERATURE: float = 0.1
SLM_MAX_TOKENS: int = 1500
SLM_TIMEOUT_S: int = 30
SLM_MAX_RETRIES: int = 3  # includes the first attempt
SLM_BACKOFF_SECONDS: list[float] = [1.0, 2.0, 4.0]

# ---------------------------------------------------------------------------
# STT confidence thresholds (D9 — demo assumptions)
# ---------------------------------------------------------------------------
# Flag segment if exp(avg_logprob) < this value
STT_LOW_CONF: float = 0.55  # corresponds to avg_logprob ~ -0.60
STT_NO_SPEECH_PROB_MAX: float = 0.60  # flag segment if no_speech_prob exceeds this

# ---------------------------------------------------------------------------
# OCR confidence thresholds
# ---------------------------------------------------------------------------
OCR_CONF_MIN: float = 0.75   # line confidence below this → unreadable flag
# (Tesseract returns 0–100; we normalise to 0.0–1.0 in ocr.py)

# ---------------------------------------------------------------------------
# Uncertain escalation (D3)
# ---------------------------------------------------------------------------
UNCERTAIN_ESCALATE_MIN: int = 20  # minutes waiting → show amber marker

# ---------------------------------------------------------------------------
# Override reason minimum length (enforced in db.py)
# ---------------------------------------------------------------------------
OVERRIDE_REASON_MIN_LEN: int = 10

# ---------------------------------------------------------------------------
# Red-flag rules table (rules.py reads this)
# Each rule: {id, label, floor, patterns_key}
# patterns_key maps to the RULES_PATTERNS dict below
# ---------------------------------------------------------------------------
RULES_TABLE = [
    # ---- CRITICAL ----
    {
        "rule_id": "R01",
        "label": "Unconscious / not responding reported",
        "floor": "CRITICAL",
        "patterns_key": "unconscious",
    },
    {
        "rule_id": "R02",
        "label": "Severe / uncontrolled bleeding reported",
        "floor": "CRITICAL",
        "patterns_key": "severe_bleeding",
    },
    {
        "rule_id": "R03",
        "label": "Facial droop / slurred speech / one-sided weakness reported",
        "floor": "CRITICAL",
        "patterns_key": "stroke_signs",
    },
    {
        "rule_id": "R04",
        "label": "Seizure / convulsion reported",
        "floor": "CRITICAL",
        "patterns_key": "seizure",
    },
    {
        "rule_id": "R05",
        "label": "Snake bite reported",
        "floor": "CRITICAL",
        "patterns_key": "snake_bite",
    },
    {
        "rule_id": "R06",
        "label": "Pregnancy with bleeding reported",
        "floor": "CRITICAL",
        "patterns_key": "pregnancy_bleeding",
    },
    {
        "rule_id": "R07",
        "label": "Suicidal statement with intent or plan reported",
        "floor": "CRITICAL",
        "patterns_key": "suicidal_intent",
    },
    {
        "rule_id": "R08",
        "label": "Chest pain with associated high-risk symptom reported",
        "floor": "CRITICAL",
        "patterns_key": "chest_pain_plus",  # handled specially in rules.py
    },
    # ---- HIGH ----
    {
        "rule_id": "R09",
        "label": "Chest pain reported",
        "floor": "HIGH",
        "patterns_key": "chest_pain",
    },
    {
        "rule_id": "R10",
        "label": "Shortness of breath reported",
        "floor": "HIGH",
        "patterns_key": "breathlessness",
    },
    {
        "rule_id": "R11",
        "label": "Suicidal ideation without plan or intent reported",
        "floor": "HIGH",
        "patterns_key": "suicidal_ideation",
    },
]

# Vitals rules (handled separately in rules.py — these are thresholds)
# Demo assumptions — not clinically validated.
VITALS_RULES = {
    "SPO2": {
        "CRITICAL_thresh": 90,  # SpO2 < 90 → CRITICAL
        "HIGH_thresh": 92,       # SpO2 < 92 → HIGH
        "direction": "below",
    },
    "HR": {
        "CRITICAL_thresh_high": 150,
        "CRITICAL_thresh_low": 30,
        "HIGH_thresh_high": 130,
        "HIGH_thresh_low": 40,
        "direction": "both",
    },
    "SBP": {
        "CRITICAL_thresh_high": 200,
        "CRITICAL_thresh_low": 80,
        "HIGH_thresh_high": 180,
        "HIGH_thresh_low": 90,
        "direction": "both",
    },
    "TEMP": {
        "HIGH_thresh_high": 39.5,   # °C
        "HIGH_thresh_low": 35.0,
        "direction": "both",
    },
}

# Negation window: tokens within this many positions of a red-flag term
# are checked for negation words (D5)
NEGATION_WINDOW: int = 4

# Negation words (EN + Roman Hindi + Devanagari)
NEGATION_WORDS = [
    r"\bno\b", r"\bnot\b", r"\bnahi[n]?\b", r"\bnhi\b", r"\bbina\b",
    r"\bwithout\b", r"\bdenies\b", r"\bनहीं\b", r"\bनही\b",
    r"\babsent\b", r"\bnone\b",
]

# ---------------------------------------------------------------------------
# Symptom patterns for rules (used in rules.py)
# Format: list of regex pattern strings (case-insensitive, NFKC-normalised)
# Covers English + Roman Hinglish spelling variants + Devanagari
# ---------------------------------------------------------------------------
RULES_PATTERNS: dict[str, list[str]] = {
    "unconscious": [
        r"\bunconscious\b",
        r"\bnot\s+respond(?:ing)?\b",
        r"\bbehosh\b",
        r"\bbebosh\b",
        r"\bhosh\s+nahi\b",
        r"\bbehoosh\b",
        r"\bबेहोश\b",
    ],
    "severe_bleeding": [
        r"\bsevere\s+bleed(?:ing)?\b",
        r"\bkhoon\s+(?:beh|nikal)\b",
        r"\bblood\s+(?:loss|pour|gush|flow)\b",
        r"\bbahut\s+(?:zyada\s+)?khoon\b",
        r"\bखून\s+बह\b",
        r"\bरक्तस्राव\b",
        r"\bbleed(?:ing)?\s+(?:a\s+lot|uncontroll|won.t\s+stop|ruk\s+nahi)\b",
    ],
    "stroke_signs": [
        r"\bface\s+(?:dropp|tedha|droop)\b",
        r"\bfacial\s+droop\b",
        r"\bslurred?\s+speech\b",
        r"\bspeech\s+slurred?\b",
        r"\bol\s+nahi\s+(?:pa|paa)\b",
        r"\bbol\s+nahi\s+paa?\s+raha?\b",
        r"\bone[\s-]?sided\s+weakness\b",
        r"\b(?:arm|leg)\s+weakness\b",
        r"\bweakness\s+(?:in\s+)?(?:right\s+|left\s+)?(?:arm|leg)\b",
        r"\bhemi(?:plegia|paresis)\b",
        r"\blakwa\b",
        r"\bparalysis\b",
        r"\btedha\s+(?:muh|chehra|munh)\b",
        r"\bmuh\s+tedha\b",
        r"\bलकवा\b",
        r"\bचेहरा\s+टेढ़ा\b",
    ],
    "seizure": [
        r"\bseizure\b",
        r"\bconvuls(?:ion|ing)\b",
        r"\bfits?\b",
        r"\bepileptic\b",
        r"\bmirgi\b",
        r"\bdaura\b",
        r"\bझटके\b",
        r"\bमिर्गी\b",
        r"\bdaure\b",
    ],
    "snake_bite": [
        r"\bsnake\s+bite\b",
        r"\bsnakebite\b",
        r"\bsaanp(?:\s+ne\s+kaat)?\b",
        r"\bsanp\s+ne\b",
        r"\bsaap\s+ne\b",
        r"\bसांप\b",
    ],
    "pregnancy_bleeding": [
        r"\bpregnant\b.*\bbleed(?:ing)?\b",
        r"\bbleed(?:ing)?\b.*\bpregnant\b",
        r"\bgarbhavati\b.*\bkhoon\b",
        r"\bgabhroo\b.*\bbleed\b",
        r"\bpregnancy\b.*\bbleed(?:ing)?\b",
        r"\bगर्भवती\b.*\bखून\b",
    ],
    "suicidal_intent": [
        r"\bwant\s+to\s+(?:die|end\s+(?:my\s+)?life|kill\s+myself)\b",
        r"\bend\s+(?:my\s+)?life\b",
        r"\bkill\s+myself\b",
        r"\bsuicide\b",
        r"\bjeene\s+ka\s+mann\s+nahi\b",
        r"\bjina\s+nahi\s+chahta\b",
        r"\bमरना\s+चाहता\b",
        r"\bमर\s+जाना\s+चाहता\b",
        r"\bjaan\s+(?:de\s+do|deni\s+hai)\b",
    ],
    "suicidal_ideation": [
        r"\bdon.t\s+want\s+to\s+live\b",
        r"\bno\s+(?:will|reason)\s+to\s+live\b",
        r"\bjeena\s+nahi\s+chahta\b",
        r"\bzindagi\s+se\s+thak\b",
        r"\bजीना\s+नहीं\s+चाहता\b",
    ],
    "chest_pain": [
        r"\bchest\s+pain\b",
        r"\b(?:left\s+)?chest\b.{0,30}\b(?:heavy\s+)?pain\b",
        r"\bseene\s+mein\s+dard\b",
        r"\bseeney\s+mein\s+dard\b",
        r"\bsine\s+mein\s+dard\b",
        r"\bchhati\s+(?:mein\s+)?dard\b",
        r"\bchhati\s+mein\s+(?:dukh|takleef|jalan|bhari)\b",
        r"\bseene\s+mein\s+(?:dukh|takleef|jalan|bhari|bhaari)\b",
        r"\bसीने\s+में\s+दर्द\b",
        r"\bछाती\s+में\s+दर्द\b",
    ],
    "breathlessness": [
        r"\bshortness\s+of\s+breath\b",
        r"\bbreath(?:ing)?\s+(?:difficulty|problem|trouble|hard)\b",
        r"\bcan.t\s+breath(?:e)?\b",
        r"\bcannot\s+breath(?:e)?\b",
        r"\bsaans\s+(?:nahi\s+le|phool|phulna|phoolna|phoolti|kam)\b",
        r"\bsans\s+phool\b",
        r"\bsaanse\s+phool\b",
        r"\bdyspnea\b",
        r"\bsaans\s+nahi\b",
        r"\bsaans\s+lene\s+mein\b",
        r"\bसांस\s+(?:फूल|नहीं|लेने)\b",
    ],
    # chest_pain_plus is a compound rule handled in rules.py
    "associated_critical": [
        # Additional symptoms that upgrade chest_pain to CRITICAL
        r"\bsweating\b",
        r"\bpasina\b",
        r"\bpaseena\b",
        r"\bsweats?\b",
        r"\bpasine\b",
        r"\bपसीना\b",
        r"\bnausea\b",
        r"\bji\s+(?:michlana|machlana)\b",
        r"\bleft\s+arm\b",
        r"\bjaw\s+(?:pain|ache)\b",
        r"\bradiat(?:ing|es?)\b",
        r"\bsaans\s+phool\b",
        r"\bsaans\s+(?:nahi|kam)\b",
        r"\bbreath(?:ing)?\b",  # any breathing mention alongside chest pain
    ],
}

# ---------------------------------------------------------------------------
# Injection detection patterns (guardrail.py reads this)
# ---------------------------------------------------------------------------
INJECTION_PATTERNS: list[dict] = [
    {"id": "INJ01", "pattern": r"ignore\s+(?:all\s+)?(?:previous|prior)?\s*(?:instructions?|rules?|context)"},
    {"id": "INJ02", "pattern": r"disregard\s+(?:all\s+)?(?:previous|prior)?\s*(?:instructions?|rules?|context)"},
    {"id": "INJ03", "pattern": r"mark\s+me\s+(?:critical|urgent|high|emergency)"},
    {"id": "INJ04", "pattern": r"set\s+(?:my\s+)?(?:urgency|priority)\s+to"},
    {"id": "INJ05", "pattern": r"override\s+urgency"},
    {"id": "INJ06", "pattern": r"system\s+prompt"},
    {"id": "INJ07", "pattern": r"you\s+are\s+now"},
    {"id": "INJ08", "pattern": r"act\s+as\b(?!.{0,20}\bfast\b)"},  # exclude "act fast"
    {"id": "INJ09", "pattern": r"pretend\s+(?:to\s+be|you\s+are)"},
    {"id": "INJ10", "pattern": r"developer\s+mode"},
    {"id": "INJ11", "pattern": r"jailbreak"},
    {"id": "INJ12", "pattern": r"ignore\s+all\s+rules"},
    {"id": "INJ13", "pattern": r"bump\s+(?:my\s+)?(?:urgency|priority)"},
]

# ---------------------------------------------------------------------------
# Diagnosis deny-list (validate.py reads this)
# Terms not allowed in SLM output UNLESS inside a verified verbatim quote
# ---------------------------------------------------------------------------
DIAGNOSIS_DENYLIST: list[str] = [
    # Conditions
    r"\bdengue\b", r"\bmalaria\b", r"\btyphoid\b",
    r"\btuberculosis\b", r"\b(?:pulmonary\s+)?tb\b",
    r"\bpneumonia\b", r"\bcovid[\-\s]?(?:19)?\b", r"\bcorona\b",
    r"\bcancer\b", r"\btumou?r\b", r"\bcarcinoma\b",
    r"\bdiabetes\b", r"\bdiabetic\b",
    r"\bhypertension\b",  # allowed in report context
    r"\basthma\b", r"\banaemia\b", r"\banemia\b",
    r"\bjaundice\b", r"\bhepatitis\b",
    r"\bappendicitis\b", r"\bsepsis\b",
    r"\bepilepsy\b",
    r"\bdepression\b", r"\banxiety\s+disorder\b",
    r"\bheart\s+attack\b", r"\bmyocardial\s+infarction\b", r"\bmi\b",
    r"\bangina\b",
    r"\bstroke\b", r"\bcerebrovascular\b",
    r"\bkidney\s+failure\b", r"\brenal\s+failure\b",
    r"\buti\b", r"\burinary\s+tract\s+infection\b",
    r"\bgastroenteritis\b", r"\bappendix\b",
    r"\bmeningitis\b", r"\bencephalitis\b",
    r"\bmalnutrition\b", r"\brickets\b",
    # Diagnostic phrases
    r"\bdiagnosis\b", r"\bdiagnosed\s+with\b",
    r"\bsuffering\s+from\b",
    r"\blikely\s+has\b", r"\bprobably\s+has\b",
    r"\bsuspected\b", r"\bconsistent\s+with\b",
    r"\bsuggestive\s+of\b",
    r"\brule\s+out\b",
    r"\bprobable\b",
    r"\bdifferential\b",
    r"\bpatient\s+has\s+\w+\b",
]

# ---------------------------------------------------------------------------
# Conflict detection map (conflict.py reads this)
# Maps patient-denial keywords to report indicators
# ---------------------------------------------------------------------------
CONFLICT_MAP: list[dict] = [
    {
        "denial_pattern": r"\bno\s+(?:diabetes|sugar)\b|\bdiabetes\s+nahi\b|\bno\s+sugar\s+problem\b",
        "report_indicators": [r"\bhba1c\b", r"\bfasting\s+(?:blood\s+)?sugar\b", r"\bfbs\b", r"\bppbs\b"],
        "description": "Patient denied diabetes/sugar condition but report contains related test",
    },
    {
        "denial_pattern": r"\bno\s+medicines?\b|\bkoi\s+dawa\s+nahi\b|\bno\s+medication\b|\bno\s+drugs?\b",
        "report_indicators": [r"\btab\.\b", r"\bcap\.\b", r"\binj\.\b", r"\bmg\b", r"\bml\b.*\bbd|tds|od\b"],
        "description": "Patient denied taking medicines but report lists medications",
    },
    {
        "denial_pattern": r"\bno\s+(?:bp|blood\s+pressure|hypertension)\b|\bbp\s+nahi\b",
        "report_indicators": [r"\bamlodipine\b", r"\blisinopril\b", r"\blosartan\b", r"\bantihypertensive\b"],
        "description": "Patient denied blood pressure issue but report mentions BP medication",
    },
    {
        "denial_pattern": r"\bno\s+allerg(?:y|ies)\b|\bkoi\s+allergy\s+nahi\b",
        "report_indicators": [r"\ballerg(?:y|ies|ic)\b", r"\banaphylaxis\b"],
        "description": "Patient denied allergies but report records an allergy",
    },
]

# ---------------------------------------------------------------------------
# Audit log allow-list for detail fields (no free text)
# ---------------------------------------------------------------------------
AUDIT_DETAIL_MAX_STR_LEN: int = 40
AUDIT_ALLOWED_DETAIL_KEYS: set[str] = {
    "case_hash", "urgency", "cached", "attempts", "rule_id", "action",
    "version", "model", "latency_ms", "tokens_in", "tokens_out",
    "consent_language", "channel", "confidence", "source_ref",
    "override_reason_len", "dropped_items", "validator_flag",
    "pattern_id", "result", "seq", "error_code",
}
