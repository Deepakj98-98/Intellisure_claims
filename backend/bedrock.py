"""
bedrock.py — Wrappers around Amazon Bedrock Agent invocations.

FIVE AGENTS, DIRECT CALLS ONLY: Claims Intake, Policy Validation,
Adjudication, Cross-Lens, Audit Trail. Every one of these is a real
Bedrock Agent, invoked the same way, through the same invoke_agent()
function below. There is no deterministic Python fallback for any of
them — cross_lens.py exists elsewhere in this repo but is NOT
imported anywhere in this file or in app.py. If you ever see Cross-
Lens output containing the literal phrase "returned an ambiguous
decision ('empty')" — that exact wording is hardcoded Python from
cross_lens.py, not model output, and means something is importing it
again by mistake.

SCHEMA: every agent below is expected to return lowercase snake_case
JSON keys (claim_id, decision, disagreement_found, etc.) — see each
wrapper's docstring for its exact expected shape, and see
Locked_Reference_Package.md for the full, authoritative schema and
the matching Bedrock console instructions for each agent. If an
agent's console instructions still use PascalCase (Decision,
ClaimID, etc.), its output will not match what this code reads —
update that agent's instructions in the console, not this file.
"""

import os
import uuid
import json
import logging
import boto3

logger = logging.getLogger(__name__)

AWS_REGION = os.getenv("AWS_REGION", "us-east-1")

CLAIM_AGENT_ID = os.getenv("CLAIM_AGENT_ID")
CLAIM_AGENT_ALIAS = os.getenv("CLAIM_AGENT_ALIAS")

POLICY_AGENT_ID = os.getenv("POLICY_AGENT_ID")
POLICY_AGENT_ALIAS = os.getenv("POLICY_AGENT_ALIAS")

ADJUDICATION_AGENT_ID = os.getenv("ADJUDICATION_AGENT_ID")
ADJUDICATION_AGENT_ALIAS = os.getenv("ADJUDICATION_AGENT_ALIAS")

CROSS_LENS_AGENT_ID = os.getenv("CROSS_LENS_AGENT_ID")
CROSS_LENS_AGENT_ALIAS = os.getenv("CROSS_LENS_AGENT_ALIAS")

AUDIT_AGENT_ID = os.getenv("AUDIT_AGENT_ID")
AUDIT_AGENT_ALIAS = os.getenv("AUDIT_AGENT_ALIAS")

try:
    bedrock_agent_client = boto3.client("bedrock-agent-runtime", region_name=AWS_REGION)
except Exception as e:
    logger.error(f"Failed to initialize Bedrock Agent Runtime client: {str(e)}")
    bedrock_agent_client = None


def invoke_agent(agent_id: str, alias_id: str, text: str, session_id: str = None) -> str:
    """Invokes a specific Amazon Bedrock Agent and returns the
    accumulated string response. Every one of the five agent wrappers
    below calls this — there is exactly one code path that actually
    talks to Bedrock in this whole file."""
    if not agent_id or not alias_id:
        logger.error("Bedrock invocation failed: agent_id or alias_id is missing.")
        raise ValueError("Agent ID and Alias ID must be configured in environment variables.")

    if not bedrock_agent_client:
        logger.error("Bedrock Agent Runtime client is not initialized.")
        raise RuntimeError("AWS Bedrock credentials or configuration is missing.")

    if not session_id:
        session_id = str(uuid.uuid4())

    logger.info(f"Invoking Bedrock Agent '{agent_id}' (Alias: '{alias_id}') in session '{session_id}'...")

    try:
        response = bedrock_agent_client.invoke_agent(
            agentId=agent_id,
            agentAliasId=alias_id,
            sessionId=session_id,
            inputText=text,
        )
        completion = ""
        for event in response.get("completion", []):
            if "chunk" in event:
                chunk = event["chunk"]
                if "bytes" in chunk:
                    completion += chunk["bytes"].decode("utf-8")
        logger.info(f"Successfully received response from Bedrock Agent '{agent_id}' ({len(completion)} chars).")
        return completion
    except Exception as e:
        logger.error(f"Error invoking Bedrock Agent '{agent_id}': {str(e)}")
        raise e


def invoke_claim_intake(text: str, session_id: str) -> str:
    """Claims Intake Agent. Input: raw extracted PDF/DOCX text.
    Expected output shape: {"claim_id", "patient_name", "patient_dob",
    "member_id", "provider_npi", "provider_name", "state",
    "date_of_service", "diagnosis_code", "procedure_code",
    "billed_amount", "prior_auth_on_file", "supporting_documents":
    {...}, "missing_fields": []} — all lowercase snake_case."""
    logger.info("Triggering Claims Intake Agent...")
    return invoke_agent(CLAIM_AGENT_ID, CLAIM_AGENT_ALIAS, text, session_id)


def invoke_policy_validation(claim_json: str, session_id: str) -> str:
    """Policy Validation Agent. Input: Claims Intake's raw JSON string
    output. Expected output shape: {"covered", "coverage_reasoning",
    "documents_complete", "missing_documents", "policy_sections_used"}."""
    logger.info("Triggering Policy Validation Agent...")
    return invoke_agent(POLICY_AGENT_ID, POLICY_AGENT_ALIAS, claim_json, session_id)


def invoke_adjudication(claim_json: str, policy_json: str, session_id: str) -> str:
    """Adjudication Agent. Input: claim + policy validation JSON,
    combined. Expected output shape: {"decision", "reasoning",
    "policy_sections_used", "confidence", "summary",
    "human_readable_explanation"} — decision must be exactly
    "approved", "denied", or "exception" (lowercase)."""
    logger.info("Triggering Adjudication Agent...")
    combined_input = {"claim": claim_json, "policy": policy_json}
    input_text = json.dumps(combined_input, indent=2)
    return invoke_agent(ADJUDICATION_AGENT_ID, ADJUDICATION_AGENT_ALIAS, input_text, session_id)


def invoke_cross_lens(claim_json: str, policy_json: str, adjudication_json: str, session_id: str) -> str:
    """Cross-Lens Agent — the ONLY place in the pipeline that checks
    claim + policy + adjudication together for contradictions. This
    is a direct Bedrock Agent call, same as every other agent here —
    NOT cross_lens.py's deterministic logic. Expected output shape:
    {"disagreement_found", "disagreement_reasons", "requires_human_review",
    "checks_performed", "agent_commentary"}."""
    logger.info("Triggering Cross-Lens Agent...")
    combined_input = {"claim": claim_json, "policy": policy_json, "adjudication": adjudication_json}
    input_text = json.dumps(combined_input, indent=2)
    return invoke_agent(CROSS_LENS_AGENT_ID, CROSS_LENS_AGENT_ALIAS, input_text, session_id)


def invoke_audit(full_trail_json: str, session_id: str) -> str:
    """Audit Trail Agent. Input: the complete claim trail (claim,
    policy, decision, cross-lens result), as one JSON string. Expected
    output shape: {"audit_status", "notes", "concerns_raised"}."""
    logger.info("Triggering Audit Trail Agent...")
    return invoke_agent(AUDIT_AGENT_ID, AUDIT_AGENT_ALIAS, full_trail_json, session_id)


def parse_json_response(text: str) -> dict:
    """Strips markdown code fences if present, then parses JSON.
    Returns {"raw_text": text} on failure rather than raising, so a
    single unparseable agent response doesn't crash the pipeline —
    downstream code checks for this shape via
    guardrails.validate_agent_output_structure()."""
    cleaned = text.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    cleaned = cleaned.strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        logger.warning("Failed to parse agent response as JSON. Returning raw text wrapped in dict.")
        return {"raw_text": text}
