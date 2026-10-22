# CareFlow AI — Non-Negotiables

These rules are enforced in code AND must not be violated by any prompt,
UI change, or refactoring. Any PR that breaks these must not be merged.

## 1. NO DIAGNOSIS — EVER
The system NEVER names a disease, condition, or diagnosis.
It outputs: symptom summaries, urgency signals, missing info, follow-up
questions, and conflicts ONLY.
The `validate.py` module redacts disease terms and flags violations.
Wording is symptom-only: "Chest pain reported" NOT "possible heart attack".

## 2. Every AI Claim Has a Verifiable Source Ref
All SLM output items must have a `source_ref` from the case's RefRegistry.
Invalid refs are dropped by the validator — never shown to clinicians.

## 3. UNKNOWN, Not a Guess
If onset, duration, severity, or any field is unclear, use the string
"UNKNOWN". The SLM is explicitly instructed not to guess.

## 4. Rules Floor + SLM — Rules Win
`merge_urgency(rules_floor, slm_suggestion)` uses URGENCY_MERGE_ORDER.
The SLM can raise urgency, NEVER lower it below the rules floor.

## 5. Patient Text Is DATA, Not Instructions
All text channels run through `guardrail.scan()` before reaching the SLM.
Matched injection spans are masked with `[REMOVED_SUSPICIOUS_INSTRUCTION]`.
The original text is stored for the clinician only.

## 6. Clinician Has Final Authority
`cases.final_urgency` is NULL until a clinician explicitly acts.
`ai_outputs` holds the suggestion only. The distinction is always shown.

## 7. Privacy — No PHI Anywhere Outside Patient Data Tables
Audit log entries are validated against an allow-list (enums, IDs, booleans,
numbers, short codes only). Any string > 40 chars or key outside allow-list
raises an error. Patient text never enters audit logs.
Erasure deletes all case rows, files, transcripts; audit rows remain (no PHI).

## 8. Graceful Failure
On API failure: retry with exponential backoff → cached output → UNCERTAIN fallback.
The demo MUST work in `cached` mode with Wi-Fi off. Cached outputs are clearly
labelled in the UI (`meta.cached = true`).

## 14. Suicidal Statements
Rule floor: HIGH (CRITICAL if intent/plan words present).
Patient page shows calm support message + Tele-MANAS 14416.
Verify this number is current before any real-world use.
