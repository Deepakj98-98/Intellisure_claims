import os
import uuid
import logging
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware

# Import custom helpers
from pdf import extract_pdf_text
from aws import upload_claim_pdf, save_claim_resolution
from bedrock import (
    invoke_claim_intake,
    invoke_policy_validation,
    invoke_adjudication,
    invoke_audit,
    parse_json_response
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("IntelliSure_Backend")

app = FastAPI(
    title="IntelliSure AI API",
    description="Backend service orchestrating multi-agent Claims Exception Resolution using Amazon Bedrock",
    version="1.0.0"
)

# Enable CORS for all origins (required for dev environments and hackathons)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health", summary="Health check endpoint")
async def health_check():
    """
    Simple health verification endpoint for orchestrators and container runners.
    """
    logger.debug("Health check called")
    return {"status": "healthy"}

@app.post("/upload", summary="Upload claims PDF and resolve exceptions")
async def upload_claim(file: UploadFile = File(...)):
    """
    Uploads a claim PDF, extracts its content, runs the Bedrock Agent pipeline,
    saves the final outcome to DynamoDB, and returns the response.
    """
    # 1. Validation check
    if not file.filename.lower().endswith(".pdf"):
        logger.warning(f"Rejected file with invalid extension: {file.filename}")
        raise HTTPException(
            status_code=400,
            detail="Invalid file format. Only PDF files are accepted."
        )
        
    session_id = str(uuid.uuid4())
    logger.info(f"Starting claim resolution pipeline for file: {file.filename} (Session: {session_id})")
    
    try:
        file_bytes = await file.read()
    except Exception as e:
        logger.error(f"Failed to read uploaded file: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to read file contents: {str(e)}"
        )
        
    # 2. Upload original PDF to S3
    try:
        s3_uri = upload_claim_pdf(file_bytes, file.filename)
    except Exception as e:
        logger.error(f"S3 Upload failed for {file.filename}: {str(e)}")
        raise HTTPException(
            status_code=502,
            detail=f"Failed to store PDF on Amazon S3: {str(e)}"
        )

    # 3. Extract text using PyMuPDF
    try:
        pdf_text = extract_pdf_text(file_bytes)
        if not pdf_text.strip():
            raise ValueError("Extracted text is empty. PDF might be empty or image-only scanned.")
    except Exception as e:
        logger.error(f"PDF extraction failed for {file.filename}: {str(e)}")
        raise HTTPException(
            status_code=422,
            detail=f"Failed to extract readable text from PDF: {str(e)}"
        )

    # 4. Invoke Claim Intake Agent
    try:
        claim_raw = invoke_claim_intake(pdf_text, session_id)
        claim_json = parse_json_response(claim_raw)
    except Exception as e:
        logger.error(f"Claim Intake Agent failed: {str(e)}")
        raise HTTPException(
            status_code=502,
            detail=f"Claim Intake Agent error: {str(e)}"
        )

    # 5. Invoke Policy Validation Agent
    try:
        # Pass the claim intake agent response as input
        policy_raw = invoke_policy_validation(claim_raw, session_id)
        policy_json = parse_json_response(policy_raw)
    except Exception as e:
        logger.error(f"Policy Validation Agent failed: {str(e)}")
        raise HTTPException(
            status_code=502,
            detail=f"Policy Validation Agent error: {str(e)}"
        )

    # 6. Invoke Adjudication Agent
    try:
        # Pass BOTH claim intake and policy validation raw outputs
        decision_raw = invoke_adjudication(claim_raw, policy_raw, session_id)
        decision_json = parse_json_response(decision_raw)
    except Exception as e:
        logger.error(f"Adjudication Agent failed: {str(e)}")
        raise HTTPException(
            status_code=502,
            detail=f"Adjudication Agent error: {str(e)}"
        )

    # 7. Invoke Audit Agent
    try:
        # Pass the adjudication decision raw output
        audit_raw = invoke_audit(decision_raw, session_id)
        audit_json = parse_json_response(audit_raw)
    except Exception as e:
        logger.error(f"Audit Agent failed: {str(e)}")
        raise HTTPException(
            status_code=502,
            detail=f"Audit Agent error: {str(e)}"
        )

    # 8. Extract ClaimID for DynamoDB
    claim_id = None
    if isinstance(claim_json, dict):
        for key in ["ClaimID", "claim_id", "ClaimNumber", "claim_number"]:
            if key in claim_json and claim_json[key]:
                claim_id = str(claim_json[key])
                break
                
    if not claim_id:
        claim_id = f"CLAIM-{uuid.uuid4().hex[:12].upper()}"
        logger.warning(f"Could not extract ClaimID from Intake response. Falling back to generated ID: {claim_id}")

    # 9. Save complete result to DynamoDB
    try:
        # Determine status from decision or default to RESOLVED
        status = "RESOLVED"
        if isinstance(decision_json, dict):
            status = decision_json.get("status", decision_json.get("Status", "RESOLVED")).upper()
            
        save_claim_resolution(
            claim_id=claim_id,
            claim_data=claim_json,
            policy_data=policy_json,
            decision_data=decision_json,
            audit_data=audit_json,
            status=status
        )
    except Exception as e:
        logger.error(f"DynamoDB storage failed: {str(e)}")
        # Since DynamoDB failed, we raise a 502, but we log all variables.
        raise HTTPException(
            status_code=502,
            detail=f"Failed to record claim details in DynamoDB database: {str(e)}"
        )

    # 10. Return complete payload response
    return {
        "claim": claim_json,
        "policy": policy_json,
        "decision": decision_json,
        "audit": audit_json
    }

if __name__ == "__main__":
    import uvicorn
    # Allow port mapping from env for flexible runs
    port = int(os.getenv("PORT", 8000))
    host = os.getenv("HOST", "0.0.0.0")
    logger.info(f"Starting server on {host}:{port}")
    uvicorn.run("app:app", host=host, port=port, reload=True)
