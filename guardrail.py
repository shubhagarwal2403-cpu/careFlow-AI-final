"""
guardrail.py — CareFlow AI injection detection and text masking
Scans all input channels (text, voice transcript, OCR lines) for
prompt-injection attempts. Masks matched spans before SLM sees the text.

Patient text is DATA, not instructions. This module enforces that boundary.
"""

import re
from typing import Optional
from config import INJECTION_PATTERNS


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def scan(
    text: str,
    channel: str,
    source_ref: str,
) -> dict:
    """
    Scan text for injection patterns.

    Returns:
        {
            "flagged": bool,
            "matches": [{"span": str, "start": int, "end": int, "pattern_id": str}],
            "masked_text": str,  # original with matched spans replaced
        }
    """
    matches = []
    masked = text

    for pat in INJECTION_PATTERNS:
        pattern_id = pat["id"]
        pattern = pat["pattern"]
        for m in re.finditer(pattern, text, flags=re.IGNORECASE | re.UNICODE):
            matches.append({
                "span": m.group(0),
                "start": m.start(),
                "end": m.end(),
                "pattern_id": pattern_id,
            })

    if matches:
        # Patterns can overlap (e.g. "ignore all rules" and "mark me critical"
        # both match inside one phrase). Merge overlapping spans first, then
        # replace right-to-left so offsets stay valid. Without the merge, the
        # second replacement re-reads already-modified text and duplicates
        # fragments such as "OUS_INSTRUCTION]".
        spans = sorted((m["start"], m["end"]) for m in matches)
        merged: list[tuple[int, int]] = []
        for start, end in spans:
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))

        for start, end in reversed(merged):
            masked = masked[:start] + "[REMOVED_SUSPICIOUS_INSTRUCTION]" + masked[end:]

    return {
        "flagged": len(matches) > 0,
        "matches": matches,
        "masked_text": masked,
    }


def scan_all_channels(
    texts: list[tuple[str, str, str]],
) -> tuple[bool, list[dict], list[str]]:
    """
    Scan multiple (text, channel, source_ref) tuples.

    Returns:
        (any_flagged, all_matches_with_ref, masked_texts)
    """
    any_flagged = False
    all_matches = []
    masked_texts = []

    for text, channel, source_ref in texts:
        result = scan(text, channel, source_ref)
        masked_texts.append(result["masked_text"])
        if result["flagged"]:
            any_flagged = True
            for m in result["matches"]:
                m["channel"] = channel
                m["source_ref"] = source_ref
                all_matches.append(m)

    return any_flagged, all_matches, masked_texts
