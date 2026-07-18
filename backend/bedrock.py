"""
bedrock.py — Wrappers around Amazon Bedrock Agent invocations.

CURRENT PIPELINE CONTEXT (see app.py for the full picture): this
module wraps FIVE Bedrock Agents — Claim Intake, Policy Validation,
Adjudication, Cross-Lens Reconciliation, and Audit. Execution
(notifications.py) is deliberately NOT a Bedrock Agent — sending a
notification is a deterministic action, not a reasoning task.

CROSS-LENS RECONCILIATION IS NOW A DIRECT, REQUIRED AGENT CALL —
NOT AN OPTIONAL SUPPLEMENT. Earlier versions of this pipeline ran
deterministic hard-override checks (prior-auth conflict, fraud
watchlist, high-value threshold) in plain Python via cross_lens.py,
with an optional Bedrock agent layered on top for subtler pattern
checks only. That module still exists in this repo but is
intentionally NOT imported by app.py right now — see app.py's module
docstring for the full reasoning. The practical consequence: the
Cross-Lens Agent's OWN console instructions must cover the full
reconciliation scope itself (prior-auth, fraud, high-value, ambiguity,
AND subtler pattern checks) — it is no longer a "check subtler things
only" agent layered on top of something else.
"""

import os
import uuid
import json
import logging
import boto3
from decimal import Decimal

logger = logging.getLogger(name)

Config from environment variables

AWS_REGION = os.getenv("AWS_REGION", "us-east-1")

CLAIM_AGENT_ID = os.getenv("CLAIM_AGENT_ID")
CLAIM_AGENT_ALIAS = os.getenv("CLAIM_AGENT_ALIAS")

POLICY_AGENT_ID = os.getenv("POLICY_AGENT_ID")
POLICY_AGENT_ALIAS = os.getenv("POLICY_AGENT_ALIAS")

ADJUDICATION_AGENT_ID = os.getenv("ADJUDICATION_AGENT_ID")
ADJUDICATION_AGENT_ALIAS = os.getenv("ADJUDICATION_AGENT_ALIAS")

Cross-Lens Reconciliation — REQUIRED for this pipeline to run (see

module docstring). Unlike the earlier design, there's no deterministic

fallback if this isn't configured — an unset CROSS_LENS_AGENT_ID will

cause invoke_cross_lens() to raise, same as any other agent's ID being missing.

CROSS_LENS_AGENT_ID = os.getenv("CROSS_LENS_AGENT_ID")
CROSS_LENS_AGENT_ALIAS = os.getenv("CROSS_LENS_AGENT_ALIAS")

AUDIT_AGENT_ID = os.getenv("AUDIT_AGENT_ID")
AUDIT_AGENT_ALIAS = os.getenv("AUDIT_AGENT_ALIAS")

Initialize Bedrock Agent Runtime client

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

Wrapper functions for the claims resolution pipeline

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
Wrapper for the Cross-Lens Reconciliation Agent — invoked exactly
like every other agent in this pipeline. This agent is now
responsible for the FULL reconciliation scope: prior-authorization
conflicts, fraud signals, high-value-claim thresholds, adjudication
ambiguity, AND subtler pattern-level contradictions (e.g. a
diagnosis code that doesn't clinically support the billed
procedure). Its console instructions must reflect that full scope
— see the agent setup notes provided alongside this pipeline
version for the exact instruction text.

Args:  
    claim_json (str): Claim Intake Agent's structured output, as a JSON string.  
    policy_json (str): Policy Validation Agent's structured output, as a JSON string.  
    adjudication_json (str): Adjudication Agent's structured output, as a JSON string.  
    session_id (str): Session tracker — app.py passes claim_id,  
                       matching every other agent call for this claim.  

Returns:  
    str: Cross-Lens agent response. Expected to be a JSON string  
         shaped like:  
         {"disagreement_found": bool, "disagreement_reasons": [str],  
          "requires_human_review": bool, "checks_performed": [str],  
          "agent_commentary": str}  
         app.py parses this via parse_json_response() and uses it  
         directly to determine the claim's routing decision.  
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
    return json.loads(cleaned, parse_float=Decimal)  
except json.JSONDecodeError:  
    logger.warning("Failed to parse agent response as JSON. Returning raw text wrapped in dict.")  
    return {"raw_text": text}