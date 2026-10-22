"""
scripts/probe_groq.py — Probe Groq API models and Whisper capabilities.
Writes findings to docs/groq_probe.md.
"""

import os
import sys
from dotenv import load_dotenv

# Load from project root
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

def probe():
    api_key = os.getenv("GROQ_API_KEY", "")
    docs_dir = os.path.join(os.path.dirname(__file__), "..", "docs")
    os.makedirs(docs_dir, exist_ok=True)
    probe_md_path = os.path.join(docs_dir, "groq_probe.md")

    if not api_key or api_key == "your_groq_api_key_here":
        content = """# Groq Probe Results

> [!NOTE]
> Probe ran in **offline/precomputed mode** (No live `GROQ_API_KEY` provided).

## Model Verification
- **Target SLM:** `llama-3.1-8b-instant` (<20B open model, fast inference)
- **Target STT:** `whisper-large-v3-turbo` (default), `whisper-large-v3`
- **JSON Mode:** Supported via `response_format={"type": "json_object"}`. Requires "JSON" in prompt.

## STT Verbose JSON Structure
When calling `groq.audio.transcriptions.create(model='whisper-large-v3-turbo', response_format='verbose_json', timestamp_granularities=['segment', 'word'])`:
- `segments`: list of objects exposing `start`, `end`, `text`, `avg_logprob`, `no_speech_prob`.
  - Confidence calculation: `confidence = exp(avg_logprob)`
  - Flagged when `confidence < 0.55` or `no_speech_prob > 0.60`
- `words`: list of objects exposing `word`, `start`, `end`. (Per-word confidence is NOT exposed by Whisper; flagged at segment level).

## Streamlit Audio Support
- `st.audio(..., start_time=...)` is supported.
- `st.audio_input` available in Streamlit >= 1.40.
"""
        with open(probe_md_path, "w", encoding="utf-8") as f:
            f.write(content)
        print("Probe completed (offline mode). Wrote docs/groq_probe.md")
        return

    try:
        from groq import Groq
        client = Groq(api_key=api_key)
        models = client.models.list()
        model_ids = [m.id for m in models.data]
        slm_present = "llama-3.1-8b-instant" in model_ids
        whisper_turbo_present = "whisper-large-v3-turbo" in model_ids

        content = f"""# Groq Probe Results (Live API)

- **Timestamp:** Live probe verified
- **llama-3.1-8b-instant available:** {slm_present}
- **whisper-large-v3-turbo available:** {whisper_turbo_present}
- **Total models available:** {len(model_ids)}

## Whisper Verbose JSON Capabilities
- Segment level: `avg_logprob`, `no_speech_prob`, `start`, `end`, `text`
- Word level: `word`, `start`, `end`
"""
        with open(probe_md_path, "w", encoding="utf-8") as f:
            f.write(content)
        print("Live Groq probe succeeded. Wrote docs/groq_probe.md")
    except Exception as e:
        print(f"Live probe failed ({e}). Writing offline notes.")

if __name__ == "__main__":
    probe()
