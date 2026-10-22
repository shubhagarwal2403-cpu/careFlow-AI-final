"""
schemas.py — CareFlow AI Pydantic models and RefRegistry
All data contracts live here: SLM output, internal types, source references.
"""

from __future__ import annotations
import re
from typing import Optional, Literal
from pydantic import BaseModel, field_validator, model_validator


# ---------------------------------------------------------------------------
# Urgency enum values (as Literal for Pydantic)
# ---------------------------------------------------------------------------
UrgencyLevel = Literal[
    "LOW",
    "UNCERTAIN_NEEDS_FOLLOWUP",
    "MEDIUM",
    "HIGH",
    "CRITICAL",
]

IssueType = Literal[
    "INCOMPLETE_INPUT",
    "UNREADABLE_OCR",
    "LOW_AUDIO_CONFIDENCE",
    "AMBIGUOUS",
]


# ---------------------------------------------------------------------------
# Sub-models (items inside the SLM output)
# ---------------------------------------------------------------------------

class ChiefComplaint(BaseModel):
    """The patient's main presenting complaint."""
    text: str
    source_ref: str  # e.g. "text:MSG_1"


class SymptomTimeline(BaseModel):
    """One symptom with temporal and severity context."""
    symptom: str
    onset: str        # "2 days ago" or "UNKNOWN"
    duration: str     # "3 hours" or "UNKNOWN"
    progression: str  # "worsening" / "stable" / "UNKNOWN"
    severity: str     # "mild" / "moderate" / "severe" / "UNKNOWN"
    source_ref: str
    evidence_quote: Optional[str] = None  # verbatim substring of the source (D7)


class ReportMetric(BaseModel):
    """A lab/vital value read from an uploaded report."""
    test_name: str
    value: str        # raw value string
    unit: str
    reference_range: str
    status: str       # "normal" / "high" / "low" / "UNKNOWN"
    source_ref: str   # must be an ocr:OCR_LINE_n ref


class UncertaintyFlag(BaseModel):
    """Flags cases where information is missing or unreliable."""
    issue_type: IssueType
    reason: str
    source_ref: str


class WarningSignal(BaseModel):
    """A clinically important observation (symptom-only language, no diagnosis)."""
    signal: str
    source_ref: str


class ConflictAlert(BaseModel):
    """A detected contradiction between two sources."""
    summary: str      # symptom-only language; NO diagnosis terms
    source_a: str     # ref to one source
    source_b: str     # ref to the conflicting source


class FollowUpQuestion(BaseModel):
    """A polite, non-diagnostic clarification question."""
    question: str


class UrgencyReason(BaseModel):
    """Why the system is suggesting a particular urgency level."""
    reason: str
    source_ref: str


# ---------------------------------------------------------------------------
# SLM output model (strict — unknown fields rejected)
# ---------------------------------------------------------------------------

class SLMOutput(BaseModel):
    """
    The full structured output from the SLM.
    Validated by validate.py before being persisted or shown.
    """
    model_config = {"extra": "forbid"}  # reject unknown fields

    chief_complaint: ChiefComplaint
    symptom_timeline: list[SymptomTimeline] = []
    extracted_report_metrics: list[ReportMetric] = []
    uncertainty_flags: list[UncertaintyFlag] = []
    warning_signals: list[WarningSignal] = []
    conflicts: list[ConflictAlert] = []
    missing_information: list[str] = []
    follow_up_questions: list[FollowUpQuestion] = []
    reply_language: str = "en"  # en | hi | hinglish
    suggested_urgency: UrgencyLevel
    urgency_reasons: list[UrgencyReason] = []
    draft_triage_note: str = ""


# ---------------------------------------------------------------------------
# Internal models (not from the SLM; added by the pipeline)
# ---------------------------------------------------------------------------

class SecurityFlag(BaseModel):
    """Records a guardrail injection detection."""
    matched_span: str
    channel: str          # text | voice | ocr
    source_ref: str
    pattern_id: str


class RuleHit(BaseModel):
    """Records a deterministic rule firing."""
    rule_id: str
    label: str            # symptom-only label
    floor: UrgencyLevel
    source_ref: str
    negated: bool = False


class CallMeta(BaseModel):
    """Metadata about the SLM API call."""
    model: str
    latency_ms: float
    tokens_in: int
    tokens_out: int
    cached: bool = False
    attempts: int = 1
    dropped_items: list[str] = []  # refs that failed validation


class PipelineResult(BaseModel):
    """
    The full output of pipeline.process_case().
    This is persisted as JSON in ai_outputs.output_json.
    """
    model_config = {"extra": "forbid"}

    slm_output: SLMOutput
    security_flags: list[SecurityFlag] = []
    rule_hits: list[RuleHit] = []
    meta: CallMeta
    # The final merged urgency (rules floor + SLM)
    final_suggested_urgency: UrgencyLevel
    # Was any injection masking applied?
    injection_detected: bool = False


# ---------------------------------------------------------------------------
# Reference Registry (D1, D7 — source grounding)
# ---------------------------------------------------------------------------

class SourceRef(BaseModel):
    """A resolved source reference with its content and location."""
    ref: str
    channel: str             # text | voice | ocr | vitals
    text: str                # the content at this ref
    bbox: Optional[list[int]] = None        # [x, y, w, h] for OCR
    start_s: Optional[float] = None         # audio start in seconds
    end_s: Optional[float] = None           # audio end in seconds
    confidence: Optional[float] = None


class RefRegistry:
    """
    Tracks all valid source references for a case.
    Built during intake and updated on follow-up answers.
    The validator uses is_valid() to drop hallucinated refs.
    """

    def __init__(self) -> None:
        self._refs: dict[str, SourceRef] = {}

    def register_text(self, msg_index: int, text: str) -> str:
        """Register a text message. Returns the ref string."""
        ref = f"text:MSG_{msg_index}"
        self._refs[ref] = SourceRef(ref=ref, channel="text", text=text)
        return ref

    def register_voice_segment(
        self,
        start_s: float,
        end_s: float,
        text: str,
        confidence: Optional[float] = None,
    ) -> str:
        """Register a voice segment. Returns the ref string."""
        start_mm_ss = _seconds_to_mm_ss(start_s)
        end_mm_ss = _seconds_to_mm_ss(end_s)
        ref = f"voice:{start_mm_ss}-{end_mm_ss}"
        self._refs[ref] = SourceRef(
            ref=ref,
            channel="voice",
            text=text,
            start_s=start_s,
            end_s=end_s,
            confidence=confidence,
        )
        return ref

    def register_ocr_line(
        self,
        line_index: int,
        text: str,
        bbox: Optional[list[int]] = None,
        confidence: Optional[float] = None,
    ) -> str:
        """Register an OCR line. Returns the ref string."""
        ref = f"ocr:OCR_LINE_{line_index}"
        self._refs[ref] = SourceRef(
            ref=ref,
            channel="ocr",
            text=text,
            bbox=bbox,
            confidence=confidence,
        )
        return ref

    def register_vitals(self, vital_name: str, value: str) -> str:
        """Register a vital reading. Returns the ref string."""
        ref = f"vitals:{vital_name}"
        self._refs[ref] = SourceRef(ref=ref, channel="vitals", text=value)
        return ref

    def is_valid(self, ref: str) -> bool:
        """True if the ref exists in this registry, or loosely matches a voice ref."""
        if ref in self._refs:
            return True
        # Voice refs: allow ±1s tolerance check
        return self._is_valid_voice_ref_loose(ref)

    def resolve(self, ref: str) -> Optional[SourceRef]:
        """Return the SourceRef for a given ref string, or None."""
        return self._refs.get(ref)

    def all_refs(self) -> list[str]:
        """All registered ref strings."""
        return list(self._refs.keys())

    def _is_valid_voice_ref_loose(self, ref: str) -> bool:
        """Check if a voice ref overlaps any registered segment (1s tolerance)."""
        m = re.match(r"voice:(\d+):(\d+)-(\d+):(\d+)", ref)
        if not m:
            return False
        start_s = int(m.group(1)) * 60 + int(m.group(2))
        end_s = int(m.group(3)) * 60 + int(m.group(4))
        for r_key, r_val in self._refs.items():
            if r_val.channel != "voice":
                continue
            s = r_val.start_s or 0.0
            e = r_val.end_s or 0.0
            # Overlap with 1s tolerance
            if start_s <= e + 1.0 and end_s >= s - 1.0:
                return True
        return False

    def to_dict_list(self) -> list[dict]:
        """Serialise to a list of dicts for passing to the SLM prompt."""
        return [v.model_dump() for v in self._refs.values()]


def _seconds_to_mm_ss(seconds: float) -> str:
    """Convert seconds to MM:SS string."""
    total = int(seconds)
    mm = total // 60
    ss = total % 60
    return f"{mm:02d}:{ss:02d}"
