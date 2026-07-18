"""
app.py — FastAPI orchestration layer for CGI IntelliSure AI.

FIVE AGENTS, DIRECT BEDROCK CALLS: Claims Intake -> Policy Validation
-> Adjudication -> Cross-Lens -> Audit Trail. Every field this file
reads from an agent's output goes through guardrails.get_field(),
which reads the LOCKED lowercase key and logs a loud warning if it
had to fall back to an old casing — see guardrails.py's module
docstring for why this exists (a real bug: Cross-Lens's Bedrock
Agent looking for "decision" while Adjudication's console instructions
were still returning "Decision").

cross_lens.py IS NOT IMPORTED ANYWHERE IN THIS FILE. If you ever need
to confirm that at a glance: search this file for the string
"cross_lens" — the only matches should be the dict key
stage_results["cross_lens"] and the DynamoDB stage name
"CROSS_LENS_RECONCILIATION", never an import statement.

Execution (notifications.py) stays plain Python, not a sixth agent —
sending a notification is a deterministic action, not a reasoning task.
"""

import os
import json
import logging
from contextlib import asynccontextmanager

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
from queue_manager import ClaimQueue

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("IntelliSure_Backend")


async def run_claim_pipeline(claim_id: str, filename: str, file_bytes: bytes) -> dict:
    """The full 5-agent pipeline for ONE claim, run through
    ClaimQueue's FIFO worker (see the bottom of this file)."""
    stage_results = {}

    # ---- Stage 0: Extract text + upload original file to S3 ----
    try:
        claim_text = extract_pdf_text(file_bytes)
        if not claim_text.strip():
            raise ValueError("Extracted text is empty. Document might be empty or image-only scanned.")
    except Exception as e:
        logger.error(f"[{claim_id}] Text extraction failed: {redact_pii(str(e))}")
        save_claim_stage(claim_id, "TEXT_EXTRACTION", {"error": str(e)}, status="FAILED")
        return _final_response(claim_id, "MISSING_INFORMATION", stage_results, failure_reason=f"Text extraction failed: {e}")

    try:
        s3_uri = upload_claim_pdf(file_bytes, filename)
        save_claim_stage(claim_id, "S3_UPLOAD", {"s3_uri": s3_uri}, status="SUCCESS")
    except Exception as e:
        logger.error(f"[{claim_id}] S3 upload failed: {redact_pii(str(e))}")
        save_claim_stage(claim_id, "S3_UPLOAD", {"error": str(e)}, status="FAILED")
        # Not fatal — we still have claim_text in memory, continue.

    # ---- Stage 1: Claims Intake Agent ----
    try:
        intake_raw = invoke_claim_intake(claim_text, claim_id)
        intake_json = parse_json_response(intake_raw)
        warnings = validate_agent_output_structure("Claims Intake Agent", intake_json)
        save_claim_stage(claim_id, "INTAKE", intake_json, status="SUCCESS" if not warnings else "SUCCESS_WITH_WARNINGS")
        stage_results["claim"] = intake_json
        if warnings:
            logger.warning(f"[{claim_id}] Intake warnings: {warnings}")
    except Exception as e:
        logger.error(f"[{claim_id}] Claims Intake Agent failed: {redact_pii(str(e))}")
        save_claim_stage(claim_id, "INTAKE", {"error": str(e)}, status="FAILED")
        return _final_response(claim_id, "MISSING_INFORMATION", stage_results, failure_reason=f"Claims Intake Agent failed: {e}")

    # ---- Stage 2: Policy Validation Agent ----
    try:
        policy_raw = invoke_policy_validation(intake_raw, claim_id)
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
        decision_raw = invoke_adjudication(intake_raw, policy_raw, claim_id)
        decision_json = parse_json_response(decision_raw)
        warnings = validate_agent_output_structure("Adjudication Agent", decision_json)
        save_claim_stage(claim_id, "ADJUDICATION", decision_json, status="SUCCESS" if not warnings else "SUCCESS_WITH_WARNINGS")
        stage_results["decision"] = decision_json
    except Exception as e:
        logger.error(f"[{claim_id}] Adjudication Agent failed: {redact_pii(str(e))}")
        save_claim_stage(claim_id, "ADJUDICATION", {"error": str(e)}, status="FAILED")
        return _final_response(claim_id, "NEEDS_HUMAN_REVIEW", stage_results, failure_reason=f"Adjudication Agent failed: {e}")

    # ---- Stage 4: Cross-Lens Agent — DIRECT Bedrock call, cross_lens.py never touched ----
    try:
        cross_lens_raw = invoke_cross_lens(intake_raw, policy_raw, decision_raw, claim_id)
        reconciliation = parse_json_response(cross_lens_raw)
        warnings = validate_agent_output_structure("Cross-Lens Agent", reconciliation)
        if warnings:
            logger.warning(f"[{claim_id}] Cross-Lens warnings: {warnings}")
        save_claim_stage(claim_id, "CROSS_LENS_RECONCILIATION", reconciliation,
                          status="DISAGREEMENT_FOUND" if get_field("Cross-Lens Agent", reconciliation, "disagreement_found", "DisagreementFound", default=False) else "CONSISTENT")
        stage_results["cross_lens"] = reconciliation
    except Exception as e:
        logger.error(f"[{claim_id}] Cross-Lens Agent failed: {redact_pii(str(e))}")
        save_claim_stage(claim_id, "CROSS_LENS_RECONCILIATION", {"error": str(e)}, status="FAILED")
        reconciliation = {
            "disagreement_found": True,
            "requires_human_review": True,
            "disagreement_reasons": [f"Cross-Lens Agent call itself failed: {e} — defaulting to human review rather than trusting an unverified decision."],
        }
        stage_results["cross_lens"] = reconciliation

    # ---- Determine final routing decision ----
    # get_field() tries the locked lowercase key first ("decision",
    # "requires_human_review"), then falls back to the old PascalCase
    # names and LOGS A WARNING if it had to — this is what would have
    # caught today's bug immediately instead of three stages downstream.
    requires_human_review = get_field("Cross-Lens Agent", reconciliation, "requires_human_review", "RequiresHumanReview", default=False)
    if requires_human_review:
        routing_decision = "NEEDS_HUMAN_REVIEW"
    else:
        decision_value = str(get_field("Adjudication Agent", decision_json, "decision", "Decision", default="")).lower()
        routing_decision = "AUTO_APPROVED" if decision_value == "approved" else "NEEDS_HUMAN_REVIEW"

    # ---- Stage 5: Execution — plain Python, not a Bedrock agent ----
    try:
        notification = send_claim_notification(
            claim_id=claim_id,
            routing_decision=routing_decision,
            claim_json=intake_json,
            reasons=get_field("Cross-Lens Agent", reconciliation, "disagreement_reasons", "DisagreementReasons", default=[]),
        )
        save_notification_record(claim_id, notification)
        stage_results["notification"] = notification
    except Exception as e:
        logger.error(f"[{claim_id}] Execution/notification failed: {redact_pii(str(e))}")
        save_claim_stage(claim_id, "EXECUTION_NOTIFICATION", {"error": str(e)}, status="FAILED")
        stage_results["notification"] = {"send_status": "FAILED", "error": str(e)}

    # ---- Stage 6: Audit Trail Agent ----
    try:
        full_trail_summary = {
            "claim": intake_json, "policy": policy_json, "decision": decision_json,
            "cross_lens": reconciliation, "routing_decision": routing_decision,
        }
        audit_raw = invoke_audit(json.dumps(full_trail_summary, default=str), claim_id)
        audit_json = parse_json_response(audit_raw)
        save_claim_stage(claim_id, "AUDIT", audit_json, status="SUCCESS")
        stage_results["audit"] = audit_json
    except Exception as e:
        logger.error(f"[{claim_id}] Audit Trail Agent failed: {redact_pii(str(e))}")
        save_claim_stage(claim_id, "AUDIT", {"error": str(e)}, status="FAILED")
        stage_results["audit"] = {"error": str(e)}

    return _final_response(claim_id, routing_decision, stage_results)


def _final_response(claim_id: str, routing_decision: str, stage_results: dict, failure_reason: str = None) -> dict:
    response = {"claim_id": claim_id, "routing_decision": routing_decision, **stage_results}
    if failure_reason:
        response["failure_reason"] = failure_reason
    return response


# ---------------------------------------------------------------------------
# FastAPI app + FIFO queue
# ---------------------------------------------------------------------------
claim_queue = ClaimQueue(process_fn=run_claim_pipeline)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await claim_queue.start_workers()
    logger.info("IntelliSure backend started, FIFO claim queue active.")
    yield
    logger.info("IntelliSure backend shutting down.")


app = FastAPI(
    title="IntelliSure AI API",
    description="Five direct Bedrock Agents (Claims Intake, Policy Validation, Adjudication, Cross-Lens, Audit Trail) orchestrated via FastAPI, with a plain-Python Execution/notification step and a FIFO claim queue.",
    version="3.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health_check():
    return {"status": "healthy", "queue_depth": claim_queue.queue_depth()}


@app.post("/upload")
async def upload_claim(file: UploadFile = File(...)):
    if not (file.filename.lower().endswith(".pdf") or file.filename.lower().endswith(".docx")):
        raise HTTPException(status_code=400, detail="Invalid file format. PDF or DOCX only.")
    try:
        file_bytes = await file.read()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read file contents: {str(e)}")

    result = await claim_queue.enqueue_and_wait(file.filename, file_bytes)
    return result


@app.post("/upload/batch")
async def upload_claims_batch(files: list[UploadFile] = File(...)):
    submitted = []
    for file in files:
        if not (file.filename.lower().endswith(".pdf") or file.filename.lower().endswith(".docx")):
            submitted.append({"filename": file.filename, "error": "Invalid file format", "claim_id": None})
            continue
        file_bytes = await file.read()
        claim_id, _future = await claim_queue.enqueue(file.filename, file_bytes)
        submitted.append({"filename": file.filename, "claim_id": claim_id, "status": "QUEUED"})
    return {"submitted": submitted, "queue_depth": claim_queue.queue_depth()}


@app.get("/claims/{claim_id}/status")
async def get_claim_status(claim_id: str):
    status = claim_queue.get_status(claim_id)
    if status == "NOT_FOUND":
        raise HTTPException(status_code=404, detail=f"No claim found with ID {claim_id}")
    return {"claim_id": claim_id, "status": status}


@app.get("/claims/{claim_id}")
async def get_claim(claim_id: str):
    try:
        history = get_claim_history(claim_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to read claim history: {str(e)}")
    if not history:
        raise HTTPException(status_code=404, detail=f"No records found for claim {claim_id}")
    return {"claim_id": claim_id, "stages": history}


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    host = os.getenv("HOST", "0.0.0.0")
    logger.info(f"Starting server on {host}:{port}")
    uvicorn.run("app:app", host=host, port=port, reload=True)
