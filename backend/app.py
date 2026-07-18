"""
app.py — FastAPI orchestration layer for CGI IntelliSure AI.

CURRENT PIPELINE (simplified — no FIFO queue, Cross-Lens is a direct
Bedrock Agent call like every other agent):

Upload PDF
|
v
Extract PDF Text (pdf.py)
|
v
Upload PDF to S3 (aws.py, server-side encrypted)
|
v
Claim Intake Agent            --> saved as its own DynamoDB stage record
|
v
Policy Validation Agent        --> saved as its own DynamoDB stage record
|
v
Adjudication Agent             --> saved as its own DynamoDB stage record
|
v
Cross-Lens Reconciliation Agent --> a REAL Bedrock Agent call, same
|                             pattern as every other agent in
|                             this pipeline (bedrock.invoke_cross_lens).
|                             Saved as its own DynamoDB stage
|                             record, including which checks
|                             the agent says it performed.
v
Execution Agent (notifications.py) --> sends (or mocks) the outcome
|                                 notification, saved as its own
|                                 stage record — the pipeline
|                                 ACTING on its decision, not
|                                 just deciding.
v
Audit Agent                     --> final compliance/QA pass over the
|                             WHOLE completed trail, saved as
|                             its own stage record
v
Return response (claim_id + full stage history + final routing_decision)

WHAT CHANGED FROM THE PREVIOUS VERSION, AND WHY:

1. NO FIFO QUEUE. queue_manager.py still exists in this repo and still
works (see its own file/tests) — it's just not imported or used
here right now. /upload processes a claim directly and
synchronously. Re-enabling batch/FIFO handling later is a matter of
importing ClaimQueue again and wrapping run_claim_pipeline the same
way the previous version did — nothing about run_claim_pipeline
itself needs to change to support that.


2. CROSS-LENS IS NOW A DIRECT BEDROCK AGENT CALL, NOT cross_lens.py.
cross_lens.py still exists in this repo, fully intact, and is
INTENTIONALLY NOT IMPORTED here — kept idle in case you want its
deterministic-checks-plus-optional-agent approach back later. Right
now, Cross-Lens Reconciliation is invoked exactly like every other
agent in this file: bedrock.invoke_cross_lens() -> parse_json_response().

IMPORTANT CONSEQUENCE: because the deterministic hard-override
checks in cross_lens.py (prior-auth conflict, fraud watchlist,
$5,000 high-value threshold) are no longer running in Python, the
Cross-Lens Bedrock Agent's OWN instructions must now cover that
full scope itself — not just "subtler" contradictions on top of
checks that used to happen elsewhere. See the updated agent
instructions provided alongside this code change; if the agent's
console instructions still say "subtler contradictions only," this
pipeline currently has NO hard-override safety net at all, which
defeats the point. Update the agent's instructions before treating
this as demo-ready.


3. The Cross-Lens Agent is expected to return the SAME JSON SHAPE
cross_lens.py's reconcile() used to return — this is deliberate, so
nothing downstream (routing decision logic, the notification
content, the frontend's Cross-Lens card) needed to change at all:
{
"disagreement_found": bool,
"disagreement_reasons": [str, ...],
"requires_human_review": bool,
"checks_performed": [str, ...],
"agent_commentary": str
}


4. ONLY CHANGE FROM THE PRIOR VERSION OF THIS FILE: every place this
file reads a field OUT of Adjudication's or Cross-Lens's output now
goes through guardrails.get_field(), which tries the locked
lowercase key first and logs a loud "SCHEMA DRIFT" warning if it
had to fall back to an older casing (e.g. "Decision" instead of
"decision"). This is a diagnostic addition, not a behavior change —
if both agents are already returning the locked lowercase schema,
these lines behave identically to before. Nothing else in this file
was touched.
"""



import os
import json
import logging

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from pdf import extract_pdf_text
from aws import upload_claim_pdf, save_claim_stage, get_claim_history, save_notification_record
from bedrock import (
invoke_claim_intake,
invoke_policy_validation,
invoke_adjudication,
invoke_cross_lens,
invoke_audit,
parse_json_response,
)
from notifications import send_claim_notification
from guardrails import redact_pii, validate_agent_output_structure, get_field

---------------------------------------------------------------------------

Logging — every log call that might contain claim content should go

through redact_pii() first (see guardrails.py). Structural/status-only

messages (no claim data) can log directly.

---------------------------------------------------------------------------

logging.basicConfig(
level=logging.INFO,
format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("IntelliSure_Backend")

async def run_claim_pipeline(claim_id: str, filename: str, file_bytes: bytes) -> dict:
"""The full agent pipeline for ONE claim, called directly and
synchronously by the /upload endpoint below (no queue in front of
it right now — see module docstring point 1).

RESILIENCE DESIGN UNCHANGED FROM BEFORE: each stage is wrapped in  
its own try/except. On failure, a FAILED stage record is saved  
with the specific reason, and the pipeline stops at that point,  
returning what succeeded so far rather than raising and losing  
everything.  
"""  
stage_results = {}  

# ---- Stage 0: Extract text + upload original PDF to S3 ----  
try:  
    pdf_text = extract_pdf_text(file_bytes)  
    if not pdf_text.strip():  
        raise ValueError("Extracted text is empty. PDF might be empty or image-only scanned (this pipeline does not OCR scanned images — see README.md's known limitations).")  
except Exception as e:  
    logger.error(f"[{claim_id}] PDF extraction failed: {redact_pii(str(e))}")  
    save_claim_stage(claim_id, "PDF_EXTRACTION", {"error": str(e)}, status="FAILED")  
    return _final_response(claim_id, "MISSING_INFORMATION", stage_results, failure_reason=f"PDF extraction failed: {e}")  

try:  
    s3_uri = upload_claim_pdf(file_bytes, filename)  
    save_claim_stage(claim_id, "S3_UPLOAD", {"s3_uri": s3_uri}, status="SUCCESS")  
except Exception as e:  
    logger.error(f"[{claim_id}] S3 upload failed: {redact_pii(str(e))}")  
    save_claim_stage(claim_id, "S3_UPLOAD", {"error": str(e)}, status="FAILED")  
    # Not fatal to the reasoning pipeline — we still have the  
    # extracted text in memory. Continue without the archival copy.  

# ---- Stage 1: Claim Intake Agent ----  
try:  
    claim_raw = invoke_claim_intake(pdf_text, claim_id)  
    claim_json = parse_json_response(claim_raw)  
    warnings = validate_agent_output_structure("Claim Intake Agent", claim_json)  
    save_claim_stage(claim_id, "INTAKE", claim_json, status="SUCCESS" if not warnings else "SUCCESS_WITH_WARNINGS")  
    stage_results["claim"] = claim_json  
    if warnings:  
        logger.warning(f"[{claim_id}] Intake warnings: {warnings}")  
except Exception as e:  
    logger.error(f"[{claim_id}] Claim Intake Agent failed: {redact_pii(str(e))}")  
    save_claim_stage(claim_id, "INTAKE", {"error": str(e)}, status="FAILED")  
    return _final_response(claim_id, "MISSING_INFORMATION", stage_results, failure_reason=f"Claim Intake Agent failed: {e}")  

# ---- Stage 2: Policy Validation Agent ----  
try:  
    policy_raw = invoke_policy_validation(claim_raw, claim_id)  
    policy_json = parse_json_response(policy_raw)  
    warnings = validate_agent_output_structure("Policy Validation Agent", policy_json)  
    save_claim_stage(claim_id, "POLICY_VALIDATION", policy_json, status="SUCCESS" if not warnings else "SUCCESS_WITH_WARNINGS")  
    stage_results["policy"] = policy_json  
except Exception as e:  
    logger.error(f"[{claim_id}] Policy Validation Agent failed: {redact_pii(str(e))}")  
    save_claim_stage(claim_id, "POLICY_VALIDATION", {"error": str(e)}, status="FAILED")  
    return _final_response(claim_id, "NEEDS_HUMAN_REVIEW", stage_results, failure_reason=f"Policy Validation Agent failed: {e}")  

# ---- Stage 3: Adjudication Agent ----  
try:  
    decision_raw = invoke_adjudication(claim_raw, policy_raw, claim_id)  
    decision_json = parse_json_response(decision_raw)  
    warnings = validate_agent_output_structure("Adjudication Agent", decision_json)  
    save_claim_stage(claim_id, "ADJUDICATION", decision_json, status="SUCCESS" if not warnings else "SUCCESS_WITH_WARNINGS")  
    stage_results["decision"] = decision_json  
except Exception as e:  
    logger.error(f"[{claim_id}] Adjudication Agent failed: {redact_pii(str(e))}")  
    save_claim_stage(claim_id, "ADJUDICATION", {"error": str(e)}, status="FAILED")  
    return _final_response(claim_id, "NEEDS_HUMAN_REVIEW", stage_results, failure_reason=f"Adjudication Agent failed: {e}")  

# ---- Stage 4: Cross-Lens Reconciliation Agent (direct Bedrock call) ----  
# See module docstring point 2 — this agent must now cover the FULL  
# reconciliation scope itself (prior-auth, fraud, high-value,  
# ambiguity, AND subtler pattern checks), since cross_lens.py's  
# deterministic checks are intentionally not running.  
try:  
    cross_lens_raw = invoke_cross_lens(claim_raw, policy_raw, decision_raw, claim_id)  
    reconciliation = parse_json_response(cross_lens_raw)  
    warnings = validate_agent_output_structure("Cross-Lens Reconciliation Agent", reconciliation)  
    if warnings:  
        logger.warning(f"[{claim_id}] Cross-Lens warnings: {warnings}")  
    # CHANGED: get_field() instead of a bare .get() — tries the  
    # locked lowercase key "disagreement_found" first, logs a  
    # SCHEMA DRIFT warning if it had to fall back to "DisagreementFound".  
    disagreement_found = get_field("Cross-Lens Reconciliation Agent", reconciliation, "disagreement_found", "DisagreementFound", default=False)  
    save_claim_stage(claim_id, "CROSS_LENS_RECONCILIATION", reconciliation,  
                      status="DISAGREEMENT_FOUND" if disagreement_found else "CONSISTENT")  
    stage_results["cross_lens"] = reconciliation  
except Exception as e:  
    # Same conservative philosophy as before: if Cross-Lens fails,  
    # we do NOT fall back to trusting Adjudication blindly — force  
    # human review instead, since we can't verify consistency.  
    logger.error(f"[{claim_id}] Cross-Lens Reconciliation Agent failed: {redact_pii(str(e))}")  
    save_claim_stage(claim_id, "CROSS_LENS_RECONCILIATION", {"error": str(e)}, status="FAILED")  
    reconciliation = {"disagreement_found": True, "requires_human_review": True,  
                       "disagreement_reasons": [f"Cross-Lens Reconciliation Agent call itself failed: {e} — defaulting to human review rather than trusting an unverified decision."]}  
    stage_results["cross_lens"] = reconciliation  

# ---- Determine final routing decision ----  
# CHANGED: both reads below now go through get_field() — same  
# locked-key-first, fallback-with-warning behavior as above.  
requires_human_review = get_field("Cross-Lens Reconciliation Agent", reconciliation, "requires_human_review", "RequiresHumanReview", default=False)  
if requires_human_review:  
    routing_decision = "NEEDS_HUMAN_REVIEW"  
else:  
    adjudication_decision = str(get_field("Adjudication Agent", decision_json, "decision", "Decision", default="")).lower()  
    routing_decision = "AUTO_APPROVED" if adjudication_decision == "approved" else "NEEDS_HUMAN_REVIEW"  

# ---- Stage 5: Execution Agent (send/mock notification) ----  
try:  
    notification = send_claim_notification(  
        claim_id=claim_id,  
        routing_decision=routing_decision,  
        claim_json=claim_json,  
        # CHANGED: get_field() instead of a bare .get().  
        reasons=get_field("Cross-Lens Reconciliation Agent", reconciliation, "disagreement_reasons", "DisagreementReasons", default=[]),  
    )  
    save_notification_record(claim_id, notification)  
    stage_results["notification"] = notification  
except Exception as e:  
    logger.error(f"[{claim_id}] Execution/notification failed: {redact_pii(str(e))}")  
    save_claim_stage(claim_id, "EXECUTION_NOTIFICATION", {"error": str(e)}, status="FAILED")  
    stage_results["notification"] = {"send_status": "FAILED", "error": str(e)}  

# ---- Stage 6: Audit Agent — final compliance/QA pass over everything ----  
try:  
    full_trail_summary = {  
        "claim": claim_json, "policy": policy_json, "decision": decision_json,  
        "cross_lens": reconciliation, "routing_decision": routing_decision,  
    }  
    audit_raw = invoke_audit(json.dumps(full_trail_summary, default=str), claim_id)  
    audit_json = parse_json_response(audit_raw)  
    save_claim_stage(claim_id, "AUDIT", audit_json, status="SUCCESS")  
    stage_results["audit"] = audit_json  
except Exception as e:  
    logger.error(f"[{claim_id}] Audit Agent failed: {redact_pii(str(e))}")  
    save_claim_stage(claim_id, "AUDIT", {"error": str(e)}, status="FAILED")  
    stage_results["audit"] = {"error": str(e)}  

return _final_response(claim_id, routing_decision, stage_results)

def _final_response(claim_id: str, routing_decision: str, stage_results: dict, failure_reason: str = None) -> dict:
"""Every return path from run_claim_pipeline goes through here, so
the response shape is always consistent regardless of which stage
the claim actually reached."""
response = {
"claim_id": claim_id,
"routing_decision": routing_decision,
**stage_results,
}
if failure_reason:
response["failure_reason"] = failure_reason
return response

---------------------------------------------------------------------------

FastAPI app — no lifespan/queue startup needed right now (see module

docstring point 1). If you re-enable the FIFO queue later, this is

where its startup hook goes back in.

---------------------------------------------------------------------------

app = FastAPI(
title="IntelliSure AI API",
description="Backend service orchestrating multi-agent Claims Exception Resolution using Amazon Bedrock, including a direct Cross-Lens Reconciliation Agent call.",
version="2.1.0",
)

app.add_middleware(
CORSMiddleware,
allow_origins=[""],
allow_credentials=False,  # NOTE: allow_origins=[""] combined with allow_credentials=True is invalid per the CORS spec and is silently ignored by browsers — set to False here since this API doesn't use cookies/credentialed requests.
allow_methods=[""],
allow_headers=[""],
)

@app.get("/health", summary="Health check endpoint")
async def health_check():
return {"status": "healthy"}

@app.post("/upload", summary="Upload a single claim PDF and resolve exceptions")
async def upload_claim(file: UploadFile = File(...)):
"""Processes one claim directly — no queue in front of this right
now. Concurrent requests to this endpoint are still handled safely
by FastAPI/uvicorn (each gets its own async task), they just aren't
given any explicit ordering guarantee the way the FIFO queue used
to provide. See module docstring point 1 if you need that back."""
if not file.filename.lower().endswith(".pdf"):
raise HTTPException(status_code=400, detail="Invalid file format. Only PDF files are accepted.")

try:  
    file_bytes = await file.read()  
except Exception as e:  
    raise HTTPException(status_code=500, detail=f"Failed to read file contents: {str(e)}")  

import uuid  
claim_id = f"CLAIM-{uuid.uuid4().hex[:12].upper()}"  
result = await run_claim_pipeline(claim_id, file.filename, file_bytes)  
return result

@app.get("/claims/{claim_id}", summary="Get a claim's full stage-by-stage audit history")
async def get_claim(claim_id: str):
"""Reads directly from DynamoDB — your durable source of truth,
independent of any in-memory state."""
try:
history = get_claim_history(claim_id)
except Exception as e:
raise HTTPException(status_code=502, detail=f"Failed to read claim history: {str(e)}")

if not history:  
    raise HTTPException(status_code=404, detail=f"No records found for claim {claim_id}")  

return {"claim_id": claim_id, "stages": history}

if name == "main":
import uvicorn
port = int(os.getenv("PORT", 8000))
host = os.getenv("HOST", "0.0.0.0")
logger.info(f"Starting server on {host}:{port}")
uvicorn.run("app:app", host=host, port=port, reload=True)