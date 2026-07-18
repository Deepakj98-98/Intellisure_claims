"""
guardrails.py — Responsible AI / governance enforcement layer, plus
schema-drift detection (added after finding a real casing mismatch
between an agent's actual Bedrock console instructions and what the
code expected — see get_field() below).
"""

import re
import logging

logger = logging.getLogger(__name__)

_PII_PATTERNS = [
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[REDACTED-SSN]"),
    (re.compile(r"\b(0[1-9]|1[0-2])/(0[1-9]|[12]\d|3[01])/(19|20)\d{2}\b"), "[REDACTED-DOB]"),
    (re.compile(r"\b(19|20)\d{2}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])\b"), "[REDACTED-DATE]"),
    (re.compile(r"\bMEM-\d{4,}\b"), "[REDACTED-MEMBER-ID]"),
    (re.compile(r"(?<!\d)\d{9}(?!\d)"), "[REDACTED-ID]"),
]


def redact_pii(text: str) -> str:
    """Masks PII/PHI patterns in a string before it's logged anywhere."""
    if not text:
        return text
    redacted = text
    for pattern, replacement in _PII_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def validate_agent_output_structure(stage_name: str, parsed_output: dict) -> list:
    """Minimal structural check: did the agent return usable JSON at
    all. Does NOT check individual field names — that's get_field()'s
    job below, since a missing field name is a different, more subtle
    problem than "the whole response failed to parse"."""
    warnings = []
    if not isinstance(parsed_output, dict):
        warnings.append(f"{stage_name} did not return a JSON object at all — got {type(parsed_output).__name__}")
        return warnings
    if "raw_text" in parsed_output and len(parsed_output) == 1:
        warnings.append(f"{stage_name} returned unparseable output — the model likely didn't follow the expected JSON format")
    if "error" in parsed_output:
        warnings.append(f"{stage_name} explicitly reported an error: {parsed_output.get('error')}")
    if not parsed_output:
        warnings.append(f"{stage_name} returned an empty response")
    return warnings


def get_field(stage_name: str, data: dict, primary_key: str, *fallback_keys, default=None):
    """Reads a field from an agent's parsed output, trying the
    LOCKED lowercase key first, then any fallback casings you pass in.
    If the primary key is missing but a fallback matched, this logs a
    loud warning — this is exactly the check that would have caught
    the Cross-Lens/Adjudication mismatch immediately, instead of it
    surfacing as a confusing "ambiguous decision" message three stages
    downstream.

    Args:
        stage_name: which agent this came from, for a clear log message
        data: the parsed dict to read from
        primary_key: the LOCKED schema key (e.g. "decision") — always
                     try this first
        *fallback_keys: older/alternate casings to also check (e.g.
                        "Decision") — finding a value here instead of
                        under primary_key means that agent's console
                        instructions are out of sync with the locked
                        schema and need updating
        default: returned if none of the keys are present at all

    Returns:
        The field's value, or `default` if not found under any key.
    """
    if primary_key in data:
        return data[primary_key]

    for fallback in fallback_keys:
        if fallback in data:
            logger.warning(
                f"SCHEMA DRIFT in {stage_name}: expected key '{primary_key}' but found '{fallback}' instead. "
                f"This agent's Bedrock console instructions are out of sync with the locked schema — "
                f"update them to use '{primary_key}'. Using the fallback value for now, but treat this as a bug to fix, not a working state."
            )
            return data[fallback]

    return default
