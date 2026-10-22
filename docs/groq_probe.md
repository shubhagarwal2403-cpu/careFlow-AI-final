# Groq Probe Results

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
