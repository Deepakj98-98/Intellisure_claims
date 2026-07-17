"""
app.py — FastAPI orchestration layer for CGI IntelliSure AI.

UPDATED PIPELINE (see README.md for the full diagram and reasoning):

  Upload PDF(s)
        |
        v
  [FIFO Queue] -- one or many files, processed in submission order
        |
        v
  Extract PDF Text (pdf.py)
        |
        v
  Upload PDF to S3 (aws.py, now server-side encrypted)
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
  Cross-Lens Reconciliation       --> THE new differentiator. Checks
        |                             Intake+Policy+Adjudication together
        |                             for contradictions a single-lens
        |                             agent would miss. Saved as its own
        |                             stage record, including which
        |                             specific checks were performed.
        v
  Execution Agent (notifications.py) --> sends (or mocks) the outcome
        |                                 notification, saved as its own
        |                                 stage record — this is the
        |                                 pipeline actually ACTING on
        |                                 its decision, not just deciding.
        v
  Audit Agent                     --> final compliance/QA pass over the
        |                             WHOLE completed trail, saved as
        |                             its own stage record
        v
  Return response (claim_id + full stage history + final routing_decision)

WHY THIS ORDER (Cross-Lens AFTER Adjudication, Execution AFTER
Cross-Lens, Audit LAST):
Cross-Lens needs Adjudication's actual decision to check it against
Policy Validation's — it can't run earlier. Execution needs Cross-Lens's
final verdict (including any override to human review) before it knows
what notification to send. Audit reviewing the whole completed trail
LAST (rather than the original design's position before saving) means
it's reviewing the ACTUAL final outcome, including any cross-lens
override — a more meaningful compliance check than auditing an
intermediate decision that might get overridden a step later.

RESILIENCE — THE OTHER MAJOR CHANGE:
Every stage's result is saved to DynamoDB via aws.save_claim_stage()
IMMEDIATELY after that stage completes — not batched up and saved only
at the very end. If a later stage fails, everything up to that point
is already permanently recorded, and the failure itself is ALSO saved
as a stage record with a clear reason (see the FAILED status handling
in run_claim_pipeline() below) — never a silent 502 with nothing to
show for it.
"""

import os
import json
import logging
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from pdf import extract_pdf_text
from aws import upload_claim_pdf, save_claim_stage, get_claim_history, save_notification_record
from bedrock import (
    invoke_claim_intake,
    invoke_policy_validation,
    invoke_adjudication,
    invoke_audit,
    parse_json_response,
)
from cross_lens import reconcile
from notifications import send_claim_notification
from guardrails import redact_pii, validate_agent_output_structure
from queue_manager import ClaimQueue

# ---------------------------------------------------------------------------
# Logging — every log call that might contain claim content should go
# through redact_pii() first (see guardrails.py). Structural/status-only
# messages (no claim data) can log directly.
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("IntelliSure_Backend")


# ---------------------------------------------------------------------------
# FIFO queue setup — see queue_manager.py for the full reasoning. The
# queue is created at module load time but its background worker(s)
# only start once the FastAPI app actually starts (the lifespan handler
# below), since starting an asyncio task requires a running event loop.
# ---------------------------------------------------------------------------
async def run_claim_pipeline(claim_id: str, filename: str, file_bytes: bytes) -> dict:
    """The actual 6-stage agent pipeline for ONE claim. This is the
    function the FIFO queue calls for each claim it dequeues — it
    doesn't know or care whether it was called from the single-file
    /upload endpoint or a /upload/batch submission, which is exactly
    the point: every claim goes through the identical pipeline,
    regardless of how it arrived.

    RESILIENCE DESIGN: each stage is wrapped in its own try/except.
    On failure, a FAILED stage record is saved with the specific
    reason (never a bare exception), and we stop the pipeline at that
    point — returning what succeeded so far rather than raising and
    losing everything. This directly answers "what happens on
    failure, and how do I know why" — check DynamoDB for this
    claim_id, the last stage recorded IS the answer.
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
        # S3 upload failing is NOT fatal to the reasoning pipeline —
        # we still have the extracted text in memory. Continue, but
        # note the missing archival copy in the final audit stage.
        s3_uri = None

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

    # ---- Stage 4: Cross-Lens Reconciliation (THE differentiator) ----
    try:
        reconciliation = reconcile(claim_json, policy_json, decision_json, session_id=claim_id)
        save_claim_stage(claim_id, "CROSS_LENS_RECONCILIATION", reconciliation,
                          status="DISAGREEMENT_FOUND" if reconciliation["disagreement_found"] else "CONSISTENT")
        stage_results["cross_lens"] = reconciliation
    except Exception as e:
        # A Cross-Lens failure is treated CONSERVATIVELY: if we can't
        # verify consistency, we do NOT fall back to trusting
        # Adjudication blindly — we force human review instead. This
        # is the same hard-override philosophy as the checks inside
        # cross_lens.py itself, applied to the case where the checker
        # itself breaks.
        logger.error(f"[{claim_id}] Cross-Lens Reconciliation failed: {redact_pii(str(e))}")
        save_claim_stage(claim_id, "CROSS_LENS_RECONCILIATION", {"error": str(e)}, status="FAILED")
        reconciliation = {"disagreement_found": True, "requires_human_review": True,
                           "disagreement_reasons": [f"Cross-Lens Reconciliation itself failed: {e} — defaulting to human review rather than trusting an unverified decision."]}
        stage_results["cross_lens"] = reconciliation

    # ---- Determine final routing decision ----
    if reconciliation.get("requires_human_review"):
        routing_decision = "NEEDS_HUMAN_REVIEW"
    else:
        adjudication_decision = str(decision_json.get("Decision", decision_json.get("decision", ""))).lower()
        routing_decision = "AUTO_APPROVED" if adjudication_decision == "approved" else "NEEDS_HUMAN_REVIEW"

    # ---- Stage 5: Execution Agent (send/mock notification) ----
    try:
        notification = send_claim_notification(
            claim_id=claim_id,
            routing_decision=routing_decision,
            claim_json=claim_json,
            reasons=reconciliation.get("disagreement_reasons", []),
        )
        save_notification_record(claim_id, notification)
        stage_results["notification"] = notification
    except Exception as e:
        logger.error(f"[{claim_id}] Execution/notification failed: {redact_pii(str(e))}")
        save_claim_stage(claim_id, "EXECUTION_NOTIFICATION", {"error": str(e)}, status="FAILED")
        # Notification failing doesn't change the claim's routing
        # decision — the DECISION already happened; only the member
        # communication about it failed. Worth flagging distinctly.
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
    the claim actually reached — the frontend can rely on
    `routing_decision` and `claim_id` always being present."""
    response = {
        "claim_id": claim_id,
        "routing_decision": routing_decision,
        **stage_results,
    }
    if failure_reason:
        response["failure_reason"] = failure_reason
    return response


# ---------------------------------------------------------------------------
# FastAPI app + lifespan (starts the FIFO queue's background worker(s)
# when the app starts, matching queue_manager.py's design)
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
    description="Backend service orchestrating multi-agent Claims Exception Resolution using Amazon Bedrock, with Cross-Lens Reconciliation, an Execution/notification layer, and FIFO batch processing.",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,  # NOTE: allow_origins=["*"] combined with allow_credentials=True is invalid per the CORS spec and is silently ignored by browsers — set to False here since this API doesn't use cookies/credentialed requests. If you need credentialed requests later, list explicit origins instead of "*".
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", summary="Health check endpoint")
async def health_check():
    return {"status": "healthy", "queue_depth": claim_queue.queue_depth()}


@app.post("/upload", summary="Upload a single claim PDF and resolve exceptions")
async def upload_claim(file: UploadFile = File(...)):
    """Single-file upload — enqueues onto the SAME FIFO queue batch
    uploads use (see queue_manager.py), then awaits the result. From
    the caller's perspective this behaves like the original
    synchronous endpoint; under the hood it's going through the FIFO
    queue for consistent ordering with any concurrent batch uploads."""
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Invalid file format. Only PDF files are accepted.")

    try:
        file_bytes = await file.read()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read file contents: {str(e)}")

    result = await claim_queue.enqueue_and_wait(file.filename, file_bytes)
    return result


@app.post("/upload/batch", summary="Upload multiple claim PDFs at once — processed FIFO")
async def upload_claims_batch(files: list[UploadFile] = File(...)):
    """Accepts multiple files in one request. Each is enqueued onto
    the FIFO queue in the order they appear in the request, and this
    endpoint returns IMMEDIATELY with a claim_id per file and status
    "QUEUED" — it does NOT wait for processing to complete (unlike
    /upload), since waiting for potentially many claims to fully
    process could hold the HTTP connection open for a long time.
    Poll GET /claims/{claim_id}/status for each claim_id to track
    progress, or GET /claims/{claim_id} once status shows a terminal
    state for the full result."""
    submitted = []
    for file in files:
        if not file.filename.lower().endswith(".pdf"):
            submitted.append({"filename": file.filename, "error": "Invalid file format — only PDF accepted", "claim_id": None})
            continue
        file_bytes = await file.read()
        claim_id, _future = await claim_queue.enqueue(file.filename, file_bytes)
        submitted.append({"filename": file.filename, "claim_id": claim_id, "status": "QUEUED"})

    logger.info(f"Batch upload: {len(submitted)} file(s) submitted, queue depth now {claim_queue.queue_depth()}")
    return {"submitted": submitted, "queue_depth": claim_queue.queue_depth()}


@app.get("/claims/{claim_id}/status", summary="Poll a claim's current processing status")
async def get_claim_status(claim_id: str):
    status = claim_queue.get_status(claim_id)
    if status == "NOT_FOUND":
        raise HTTPException(status_code=404, detail=f"No claim found with ID {claim_id} (it may not have been submitted, or the backend restarted since it was queued — in-memory queue state does not survive a restart, see README.md's known limitations).")
    return {"claim_id": claim_id, "status": status}


@app.get("/claims/{claim_id}", summary="Get a claim's full stage-by-stage audit history")
async def get_claim(claim_id: str):
    """Reads directly from DynamoDB (not the in-memory queue), so this
    works even after a backend restart, unlike /status above — this is
    your durable source of truth."""
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
