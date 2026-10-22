"""
stt.py — CareFlow AI speech-to-text
Providers: groq (Whisper), precomputed (JSON), bhashini (stub).
Outputs transcript + segments with confidence flags.
"""

import json
import os
import math
from typing import Optional

from config import (
    GROQ_API_KEY, STT_MODEL, STT_PROVIDER,
    STT_LOW_CONF, STT_NO_SPEECH_PROB_MAX,
)
import guardrail


def transcribe(
    audio_path: str,
    provider: Optional[str] = None,
    case_id: Optional[str] = None,
) -> dict:
    """
    Transcribe audio to text.

    Returns:
        {
            "text": str,
            "language": str,
            "segments": [{"start": float, "end": float, "text": str,
                          "confidence": float, "no_speech_prob": float,
                          "low_confidence": bool}],
            "words": [{"word": str, "start": float, "end": float}],
            "low_confidence_segments": [int],  # segment indices
            "guardrail": dict,  # result of guardrail scan on transcript
        }
    """
    _provider = provider or STT_PROVIDER

    if _provider == "groq":
        return _transcribe_groq(audio_path)
    elif _provider == "precomputed":
        return _load_precomputed(audio_path)
    elif _provider == "bhashini":
        raise NotImplementedError(
            "Bhashini STT is not yet integrated. "
            "Set STT_PROVIDER=groq or STT_PROVIDER=precomputed. "
            "Bhashini integration requires an API key from bhashini.gov.in "
            "and their SDK. See docs/bhashini_integration_notes.md."
        )
    else:
        raise ValueError(f"Unknown STT provider: {_provider}")


def _transcribe_groq(audio_path: str) -> dict:
    """Call Groq Whisper with verbose_json and segment+word timestamps."""
    from groq import Groq

    client = Groq(api_key=GROQ_API_KEY)

    with open(audio_path, "rb") as f:
        response = client.audio.transcriptions.create(
            model=STT_MODEL,
            file=f,
            response_format="verbose_json",
            timestamp_granularities=["segment", "word"],
        )

    return _parse_groq_response(response)


def _parse_groq_response(response) -> dict:
    """Parse the Groq Whisper verbose_json response."""
    # Extract segments
    raw_segments = getattr(response, "segments", []) or []
    parsed_segments = []
    low_conf_indices = []

    for i, seg in enumerate(raw_segments):
        avg_logprob = getattr(seg, "avg_logprob", -1.0)
        no_speech_prob = getattr(seg, "no_speech_prob", 0.0)

        # D9: compute confidence from avg_logprob
        confidence = math.exp(avg_logprob) if avg_logprob is not None else 0.0
        low_conf = (
            confidence < STT_LOW_CONF
            or no_speech_prob > STT_NO_SPEECH_PROB_MAX
        )

        parsed_segments.append({
            "start": getattr(seg, "start", 0.0),
            "end": getattr(seg, "end", 0.0),
            "text": getattr(seg, "text", ""),
            "confidence": confidence,
            "no_speech_prob": no_speech_prob,
            "low_confidence": low_conf,
        })

        if low_conf:
            low_conf_indices.append(i)

    # Extract words
    raw_words = getattr(response, "words", []) or []
    parsed_words = []
    for w in raw_words:
        parsed_words.append({
            "word": getattr(w, "word", ""),
            "start": getattr(w, "start", 0.0),
            "end": getattr(w, "end", 0.0),
        })

    transcript_text = getattr(response, "text", "") or ""
    language = getattr(response, "language", "unknown") or "unknown"

    # Run guardrail on the transcript
    guard_result = guardrail.scan(transcript_text, channel="voice", source_ref="voice:00:00-99:99")

    return {
        "text": transcript_text,
        "language": language,
        "segments": parsed_segments,
        "words": parsed_words,
        "low_confidence_segments": low_conf_indices,
        "guardrail": guard_result,
    }


def _load_precomputed(audio_path: str) -> dict:
    """
    Load precomputed STT JSON from next to the audio file.
    File: <audio_name>.json or stt.json in the same directory.
    """
    audio_dir = os.path.dirname(audio_path)
    audio_name = os.path.splitext(os.path.basename(audio_path))[0]

    candidates = [
        os.path.join(audio_dir, f"{audio_name}.json"),
        os.path.join(audio_dir, "stt.json"),
    ]

    for cand in candidates:
        if os.path.exists(cand):
            with open(cand, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Run guardrail on the transcript text
            guard_result = guardrail.scan(
                data.get("text", ""),
                channel="voice",
                source_ref="voice:00:00-99:99",
            )
            data["guardrail"] = guard_result
            return data

    raise FileNotFoundError(
        f"No precomputed STT JSON found for {audio_path}. "
        "Expected: stt.json in the same directory."
    )
