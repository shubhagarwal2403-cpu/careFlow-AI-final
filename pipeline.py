"""
pipeline.py — CareFlow AI orchestrator
process_case() wires together: consent → intake → STT/OCR → guardrail →
RefRegistry → rules → SLM → validate → conflicts → persist → audit.
"""

import json
import logging
import os
from typing import Optional

import db as _db
from consent import assert_consent
from guardrail import scan as guardrail_scan
from rules import check_rules
from slm import call_slm
from validate import validate_slm_output, merge_urgency
from conflict import detect_conflicts
from schemas import (
    RefRegistry, SecurityFlag, RuleHit, CallMeta,
    PipelineResult, SLMOutput, UncertaintyFlag,
)
import audit

logger = logging.getLogger(__name__)


def process_case(
    case_id: str,
    ai_mode: Optional[str] = None,
    db_path: str = _db.DB_PATH,
) -> dict:
    """
    Full pipeline for a case: intake → AI → validate → persist.
    Re-runs on follow-up answers using all cumulative inputs.

    Returns:
        A plain dict serialisation of PipelineResult.
    Raises:
        ConsentRequired if consent not given.
        CaseNotFound if case doesn't exist.
    """
    # 1. Assert consent
    assert_consent(case_id, db_path)

    # 2. Load all intakes
    intakes = _db.get_intakes(case_id, db_path)
    case = _db.get_case(case_id, db_path)

    # 3. Build RefRegistry + process each intake channel
    registry = RefRegistry()
    security_flags: list[SecurityFlag] = []
    text_for_rules: list[str] = []   # masked text per message
    text_refs: list[str] = []
    vitals: Optional[dict] = None
    texts_with_refs: list[tuple[str, str, str]] = []  # (slm_text, channel, ref)

    # For conflict detection
    text_msgs_for_conflict: list[tuple[str, str]] = []
    ocr_lines_for_conflict: list[tuple[str, str]] = []
    ocr_failures: list[str] = []

    # Full concatenation for quote verification
    all_source_text = ""

    for intake in intakes:
        channel = intake["channel"]

        if channel == "text":
            raw_text = intake["content_text"] or ""
            msg_index = intake["msg_index"] or 1
            ref = registry.register_text(msg_index, raw_text)
            all_source_text += " " + raw_text

            # Guardrail scan
            g_result = guardrail_scan(raw_text, channel="text", source_ref=ref)
            masked = g_result["masked_text"]

            if g_result["flagged"]:
                for m in g_result["matches"]:
                    security_flags.append(SecurityFlag(
                        matched_span=m["span"],
                        channel="text",
                        source_ref=ref,
                        pattern_id=m["pattern_id"],
                    ))
                audit.log_action(
                    actor_role="system",
                    action_type="guardrail_triggered",
                    case_id=case_id,
                    details={"channel": "text", "pattern_id": g_result["matches"][0]["pattern_id"]},
                    db_path=db_path,
                )

            text_for_rules.append(masked)
            text_refs.append(ref)

            # Give the SLM the follow-up question as context. Short answers
            # ("yes, since yesterday") are ambiguous on their own, and the
            # question makes the generated summary/triage note more accurate.
            # Rules, guardrail, conflict detection and evidence-quote checks
            # still use only the patient's answer (above), so symptom words in
            # the question cannot trigger false red flags or become "quotes".
            question = (intake.get("question") or "").strip()
            slm_text = (
                f"Follow-up question: {question}\nPatient's answer: {masked}"
                if question else masked
            )
            texts_with_refs.append((slm_text, "text", ref))
            text_msgs_for_conflict.append((raw_text, ref))

        elif channel == "voice":
            # Load precomputed or live STT result
            content_ref = intake["content_ref"] or ""
            stt_result = _load_voice_intake(content_ref, case_id)
            if stt_result:
                voice_refs: list[str] = []
                for i, seg in enumerate(stt_result.get("segments", [])):
                    ref = registry.register_voice_segment(
                        seg["start"], seg["end"], seg["text"],
                        confidence=seg.get("confidence"),
                    )
                    voice_refs.append(ref)
                    # Guardrail on transcript
                    g_result = guardrail_scan(seg["text"], "voice", ref)
                    if g_result["flagged"]:
                        for m in g_result["matches"]:
                            security_flags.append(SecurityFlag(
                                matched_span=m["span"],
                                channel="voice",
                                source_ref=ref,
                                pattern_id=m["pattern_id"],
                            ))

                # Add full transcript as a text input too
                full_text = stt_result.get("text", "")
                if full_text:
                    all_source_text += " " + full_text
                    # If there were no segments, register a full-transcript ref
                    # so the input still has a valid source reference.
                    transcript_ref = voice_refs[0] if voice_refs else registry.register_voice_segment(
                        0.0, 0.0, full_text
                    )
                    texts_with_refs.append((full_text, "voice", transcript_ref))
                    # Voice content must also feed the deterministic rules
                    # engine, not just the SLM.
                    text_for_rules.append(full_text)
                    text_refs.append(transcript_ref)

        elif channel == "ocr":
            content_ref = intake["content_ref"] or ""
            ocr_line_start = intake["ocr_line_start"] or 1
            ocr_result = _load_ocr_intake(content_ref, ocr_line_start, case_id)
            if not ocr_result:
                ocr_failures.append(os.path.basename(content_ref) or "uploaded report")
            else:
                for line in ocr_result.get("lines", []):
                    line_ref = registry.register_ocr_line(
                        int(line["line_id"].split("_")[-1]),
                        line["text"],
                        bbox=line.get("bbox"),
                        confidence=line.get("confidence"),
                    )
                    all_source_text += " " + line["text"]
                    # Guardrail on OCR lines
                    g_result = guardrail_scan(line["text"], "ocr", line_ref)
                    if g_result["flagged"] and not line.get("unreadable", False):
                        for m in g_result["matches"]:
                            security_flags.append(SecurityFlag(
                                matched_span=m["span"],
                                channel="ocr",
                                source_ref=line_ref,
                                pattern_id=m["pattern_id"],
                            ))
                    # For conflict detection — only readable lines
                    if not line.get("unreadable", False):
                        ocr_lines_for_conflict.append((line["text"], line_ref))
                    # For SLM (only readable lines with good confidence)
                    from config import OCR_CONF_MIN
                    conf = line.get("confidence", 0.0)
                    if not line.get("unreadable", False) and conf >= OCR_CONF_MIN:
                        texts_with_refs.append((line["text"], "ocr", line_ref))
                        # Report text must also feed the deterministic rules
                        # engine, not just the SLM.
                        text_for_rules.append(line["text"])
                        text_refs.append(line_ref)

        elif channel == "vitals":
            vitals_json = intake["content_text"] or "{}"
            try:
                vitals = json.loads(vitals_json)
            except json.JSONDecodeError:
                vitals = {}
            for k, v in vitals.items():
                registry.register_vitals(k, str(v))

    # Ensure at least one ref exists
    if not registry.all_refs():
        registry.register_text(1, "")

    # 4. Rules engine
    rules_result = check_rules(
        texts=text_for_rules,
        vitals=vitals,
        source_refs=text_refs,
    )
    rules_floor: str = rules_result["floor"]
    rule_hits: list[RuleHit] = rules_result["hits"]
    negated_hits: list[RuleHit] = rules_result["negated"]
    all_rule_hits = rule_hits + negated_hits

    # Audit rule hits
    for rh in rule_hits:
        audit.log_action(
            actor_role="system",
            action_type="rule_fired",
            case_id=case_id,
            details={"rule_id": rh.rule_id, "urgency": rh.floor},
            db_path=db_path,
        )

    # 5. Case slug for cached mode
    case_slug = _derive_case_slug(case_id, db_path)

    # 6. SLM call
    rule_hits_summary = [f"[{rh.rule_id}] {rh.label} → floor {rh.floor}" for rh in rule_hits]
    raw_output, meta_dict = call_slm(
        texts_with_refs=texts_with_refs,
        registry=registry,
        vitals=vitals,
        rule_hits_summary=rule_hits_summary if rule_hits_summary else None,
        case_slug=case_slug,
        ai_mode=ai_mode,
    )

    # 7. Validate SLM output (with retry on parse failure)
    injection_detected = len(security_flags) > 0
    slm_output, dropped_refs, final_urgency = validate_slm_output(
        raw_json=raw_output,
        registry=registry,
        rules_floor=rules_floor,
        rule_hits=all_rule_hits,
        injection_detected=injection_detected,
        case_id=case_id,
        db_path=db_path,
        source_text=all_source_text,
    )

    # If validation failed (None returned), use fallback
    if slm_output is None:
        from slm import _FALLBACK_OUTPUT
        slm_output, dropped_refs, final_urgency = validate_slm_output(
            raw_json=_FALLBACK_OUTPUT.copy(),
            registry=registry,
            rules_floor=rules_floor,
            rule_hits=all_rule_hits,
            injection_detected=injection_detected,
        )

    meta_dict["dropped_items"] = dropped_refs

    # Never let a failed report read disappear silently. The clinician sees an
    # UNREADABLE_OCR alert and can inspect the original upload.
    for failed_name in ocr_failures:
        slm_output.uncertainty_flags.append(
            UncertaintyFlag(
                issue_type="UNREADABLE_OCR",
                reason=(
                    f"Could not read uploaded report ({failed_name}). "
                    "Please inspect the original file."
                ),
                source_ref="ocr:UNREADABLE",
            )
        )

    # 8. Conflict detection
    slm_conflicts = [c.model_dump() for c in (slm_output.conflicts or [])]
    conflicts = detect_conflicts(
        text_msgs=text_msgs_for_conflict,
        ocr_lines=ocr_lines_for_conflict,
        slm_conflicts=slm_conflicts,
        registry=registry,
    )
    slm_output.conflicts = conflicts  # Replace with merged conflicts

    # 9. Build result
    call_meta = CallMeta(
        model=meta_dict.get("model", "unknown"),
        latency_ms=meta_dict.get("latency_ms", 0.0),
        tokens_in=meta_dict.get("tokens_in", 0),
        tokens_out=meta_dict.get("tokens_out", 0),
        cached=meta_dict.get("cached", False),
        attempts=meta_dict.get("attempts", 1),
        dropped_items=meta_dict.get("dropped_items", []),
    )

    result = PipelineResult(
        slm_output=slm_output,
        security_flags=security_flags,
        rule_hits=all_rule_hits,
        meta=call_meta,
        final_suggested_urgency=final_urgency,
        injection_detected=injection_detected,
    )

    # 10. Persist
    result_json = result.model_dump_json()
    _db.save_ai_output(
        case_id=case_id,
        output_json=result_json,
        suggested_urgency=final_urgency,
        latency_ms=call_meta.latency_ms,
        tokens_in=call_meta.tokens_in,
        tokens_out=call_meta.tokens_out,
        model=call_meta.model,
        cached=call_meta.cached,
        db_path=db_path,
    )

    # Update case status
    _db.update_case_status(case_id, "ready", db_path)

    # Audit the AI output event
    audit.log_action(
        actor_role="system",
        action_type="ai_output_created",
        case_id=case_id,
        details={
            "urgency": final_urgency,
            "cached": call_meta.cached,
            "attempts": call_meta.attempts,
        },
        db_path=db_path,
    )

    return json.loads(result_json)


def _load_voice_intake(content_ref: str, case_id: str) -> Optional[dict]:
    """Load STT result for a voice intake. Returns None if not found."""
    content_ref = _db.resolve_intake_path(content_ref, case_id)
    if not content_ref or not os.path.exists(content_ref):
        return None
    from stt import transcribe

    try:
        return transcribe(content_ref, provider="precomputed")
    except FileNotFoundError:
        pass
    except Exception:
        # A corrupt/partial sidecar should not block live transcription.
        pass

    # Live browser recordings (and arbitrary uploads) have no precomputed
    # sidecar. Transcribe them with the live provider when one is configured,
    # then cache the result next to the audio so pipeline re-runs and source
    # grounding reuse the same transcript.
    from config import GROQ_API_KEY, STT_PROVIDER
    if not GROQ_API_KEY or STT_PROVIDER == "bhashini":
        return None
    try:
        result = transcribe(content_ref, provider="groq")
        sidecar = os.path.splitext(content_ref)[0] + ".json"
        with open(sidecar, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False)
        return result
    except Exception:
        return None


def _load_ocr_intake(content_ref: str, ocr_line_start: int, case_id: str) -> Optional[dict]:
    """Load OCR result for a report intake. Returns None if not found."""
    if not content_ref:
        logger.warning("OCR intake for case %s has no content reference", case_id)
        return None
    content_ref = _db.resolve_intake_path(content_ref, case_id)
    if not os.path.exists(content_ref):
        logger.warning("OCR intake file missing for case %s: %s", case_id, content_ref)
        return None
    try:
        from ocr import read_report
        return read_report(content_ref, ocr_line_start=ocr_line_start)
    except Exception:
        # Do not fail the whole intake because one report could not be read,
        # but make the failure visible in the server/app logs for diagnosis.
        logger.exception("OCR extraction failed for case %s: %s", case_id, content_ref)
        return None


def _derive_case_slug(case_id: str, db_path: str) -> Optional[str]:
    """Try to derive a demo case slug from the pseudonym code."""
    try:
        case = _db.get_case(case_id, db_path)
        if case:
            code = case.get("pseudonym_code", "")
            # Demo cases use their slug as pseudonym code
            known_slugs = [
                "cardiac_hinglish", "conflict_report",
                "blurry_report", "prompt_injection", "fever_vague",
            ]
            for slug in known_slugs:
                if slug in code:
                    return slug
    except Exception:
        pass
    return None
