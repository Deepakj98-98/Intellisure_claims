"""
bedrock.py — Wrappers around Amazon Bedrock Agent invocations.

UPDATED PIPELINE CONTEXT (see app.py and README.md for the full
picture): this module wraps FIVE Bedrock Agents — Claim Intake,
Policy Validation, Adjudication, Cross-Lens Reconciliation, and
Audit. Execution (notifications.py) is deliberately NOT a Bedrock
Agent:

- Cross-Lens Reconciliation (cross_lens.py) runs its four hard-override
  checks as plain, deterministic Python FIRST — fast, reliable, and
  testable with zero AWS dependency. This module's invoke_cross_lens()
  wrapper is the OPTIONAL second half: a real Bedrock Agent call for
  subtler pattern-recognition contradictions the deterministic rules
  don't explicitly encode. The hard overrides that matter most for
  Responsible AI never depend on this call succeeding — see
  cross_lens.py's reconcile() for how the two are merged, and why a
  failure here can only ADD a disagreement finding, never remove one.
- Execution (notifications.py) sends a notification — a deterministic
  ACTION, not a reasoning task, so it's plain Python (or a real SES
  call), not an LLM invocation. Asking a language model to "decide"
  whether to send an email is the wrong tool for a deterministic
  action; save the model calls for genuine reasoning.

This is a deliberate design principle worth being able to explain if
asked: not every pipeline stage needs to be an AI call. The ones that
require judgment (Intake's extraction, Policy Validation's coverage
read, Adjudication's decision, Cross-Lens's subtler pattern check, and
Audit's final QA pass) go through Bedrock. The ones that are pure
logic (hard override rules) or pure action (sending a notification)
don't.
"""

import os
import uuid
import json
import logging
import boto3

logger = logging.getLogger(__name__)

# Config from environment variables
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

# Initialize Bedrock Agent Runtime client
try:
    bedrock_agent_client = boto3.client("bedrock-agent-runtime", region_name=AWS_REGION)
except Exception as e:
    logger.error(f"Failed to initialize Bedrock Agent Runtime client: {str(e)}")
    bedrock_agent_client = None

def invoke_agent(agent_id: str, alias_id: str, text: str, session_id: str = None) -> str:
    """
    Invokes a specific Amazon Bedrock Agent and returns the accumulated string response.
    
    Args:
        agent_id (str): The ID of the Bedrock Agent.
        alias_id (str): The Agent Alias ID.
        text (str): Input prompt or data for the agent.
        session_id (str, optional): Session ID to track conversation state. Defaults to generating one.
        
    Returns:
        str: Decoded response from the Bedrock Agent.
    """
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
            inputText=text
        )
        
        completion = ""
        # The invoke_agent response returns an event stream under the 'completion' key.
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

# Wrapper functions for the claims resolution pipeline

def invoke_claim_intake(text: str, session_id: str) -> str:
    """
    Wrapper for Claim Intake Agent.
    
    Args:
        text (str): Raw extracted claim text.
        session_id (str): Session tracker.
    
    Returns:
        str: Intake agent response (structured claim JSON string).
    """
    logger.info("Triggering Claim Intake Agent...")
    return invoke_agent(CLAIM_AGENT_ID, CLAIM_AGENT_ALIAS, text, session_id)

def invoke_policy_validation(claim_json: str, session_id: str) -> str:
    """
    Wrapper for Policy Validation Agent.
    
    Args:
        claim_json (str): Parsed claim details from Intake agent.
        session_id (str): Session tracker.
        
    Returns:
        str: Policy validation response (validation decisions).
    """
    logger.info("Triggering Policy Validation Agent...")
    return invoke_agent(POLICY_AGENT_ID, POLICY_AGENT_ALIAS, claim_json, session_id)

def invoke_adjudication(claim_json: str, policy_json: str, session_id: str) -> str:
    """
    Wrapper for Adjudication Agent.
    
    Args:
        claim_json (str): Parsed claim details from Intake agent.
        policy_json (str): Validation details from Policy agent.
        session_id (str): Session tracker.
        
    Returns:
        str: Adjudication response (approval/denial/exception decision).
    """
    logger.info("Triggering Adjudication Agent...")
    # Provide both claim and policy validations
    combined_input = {
        "claim": claim_json,
        "policy": policy_json
    }
    input_text = json.dumps(combined_input, indent=2)
    return invoke_agent(ADJUDICATION_AGENT_ID, ADJUDICATION_AGENT_ALIAS, input_text, session_id)


def invoke_cross_lens(claim_json: str, policy_json: str, adjudication_json: str, session_id: str) -> str:
    """
    Wrapper for the Cross-Lens Reconciliation Agent — the OPTIONAL
    second half of cross_lens.py's reconciliation step (the four hard-
    override rules run as deterministic Python and do not go through
    this function at all; see cross_lens.py's reconcile()).

    This wrapper follows the exact same pattern as
    invoke_claim_intake/invoke_policy_validation/invoke_adjudication
    above deliberately — one consistent shape for every agent call in
    this file, so a new teammate reading this module can predict what
    every wrapper does without re-learning a new convention per agent.

    Args:
        claim_json (str): Claim Intake Agent's structured output, as a JSON string.
        policy_json (str): Policy Validation Agent's structured output, as a JSON string.
        adjudication_json (str): Adjudication Agent's structured output, as a JSON string.
        session_id (str): Session tracker — reuse the same session_id
                           already used for this claim's other agent
                           calls, so Bedrock's session memory (if the
                           agent is configured to use it) has the full
                           conversation context.

    Returns:
        str: Cross-Lens agent response — expected to be a JSON string
             shaped like {"additional_disagreement_found": bool,
             "reason": str|null, "commentary": str}. Caller (cross_lens.py)
             is responsible for parsing this via parse_json_response()
             and merging it with the deterministic findings.

    Raises:
        ValueError: if CROSS_LENS_AGENT_ID/ALIAS aren't configured —
                    callers should check `if CROSS_LENS_AGENT_ID:` (see
                    cross_lens.py) BEFORE calling this, since this is
                    an optional stage, not a required one. This
                    function itself still raises rather than silently
                    doing nothing, so a misconfiguration is never
                    mistaken for "the agent found nothing wrong."
    """
    logger.info("Triggering Cross-Lens Reconciliation Agent...")
    combined_input = {
        "claim": claim_json,
        "policy": policy_json,
        "adjudication": adjudication_json,
    }
    input_text = json.dumps(combined_input, indent=2)
    return invoke_agent(CROSS_LENS_AGENT_ID, CROSS_LENS_AGENT_ALIAS, input_text, session_id)


def invoke_audit(decision_json: str, session_id: str) -> str:
    """
    Wrapper for Audit Agent.
    
    Args:
        decision_json (str): Adjudication decision data.
        session_id (str): Session tracker.
        
    Returns:
        str: Audit report details.
    """
    logger.info("Triggering Audit Agent...")
    return invoke_agent(AUDIT_AGENT_ID, AUDIT_AGENT_ALIAS, decision_json, session_id)

def parse_json_response(text: str) -> dict:
    """
    Cleans markdown wrappers and parses Bedrock response text into a JSON/dictionary structure.
    
    Args:
        text (str): Raw string response from Bedrock Agent.
        
    Returns:
        dict: Parsed JSON content or a wrapper dict if parsing fails.
    """
    cleaned = text.strip()
    
    # Remove markdown codeblock tags if the agent returned them
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
