"""
aws.py — S3 and DynamoDB integration.

WHAT CHANGED FROM THE ORIGINAL VERSION, AND WHY:
The original save_claim_resolution() wrote ONE combined DynamoDB item
per claim, containing every agent's output all at once, at the very
end of the pipeline. Two real problems with that:
  1. If any stage failed partway through, NOTHING was saved — you'd
     lose the record that Intake and Policy Validation succeeded, even
     though only Adjudication failed.
  2. You can't answer "show me exactly what the Policy Validation
     Agent saw and concluded" without digging through one giant nested
     blob — there's no clean per-stage audit record.

save_claim_stage() below fixes both: it writes ONE DynamoDB item per
pipeline stage, sharing claim_id as the partition key and a
stage-specific sort key. This means:
  - A claim that fails at Adjudication still has Intake and Policy
    Validation's results permanently recorded — nothing is lost.
  - `get_claim_history()` reconstructs the full stage-by-stage story
    for any claim with one Query call — this is your actual audit
    trail, and it's real, not just a slide claim.
"""

import os
from dotenv import load_dotenv
import boto3
from datetime import datetime, timezone
import logging
from decimal import Decimal

from guardrails import redact_pii

load_dotenv()

logger = logging.getLogger(__name__)

BUCKET_NAME = os.getenv("BUCKET_NAME")
CLAIMS_TABLE = os.getenv("CLAIMS_TABLE")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")

try:
    s3_client = boto3.client("s3", region_name=AWS_REGION)
    dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
except Exception as e:
    logger.error(f"Failed to initialize AWS clients: {str(e)}")
    s3_client = None
    dynamodb = None


def upload_claim_pdf(file_bytes: bytes, filename: str) -> str:
    """Uploads the original claim PDF bytes to S3. Unchanged in spirit
    from the original — this part was already correct: the raw
    document lives in exactly one place (S3), and every downstream
    stage works off EXTRACTED TEXT, not a re-read of the PDF, so
    there's no duplication of the PHI-bearing raw document across the
    system (see knowledge_base/compliance_guardrails.md's "Minimum
    Necessary" section). Added server-side encryption explicitly."""
    if not BUCKET_NAME:
        raise ValueError("BUCKET_NAME environment variable is not set.")
    if not s3_client:
        raise RuntimeError("AWS credentials or configuration is missing.")

    key = f"claims/{filename}"
    logger.info(f"Uploading file to S3 bucket '{BUCKET_NAME}' with key '{key}'...")

    try:
        s3_client.put_object(
            Bucket=BUCKET_NAME,
            Key=key,
            Body=file_bytes,
            ContentType="application/pdf",
            ServerSideEncryption="AES256",  # encryption-at-rest — see compliance_guardrails.md's honest-scope note: this covers THIS object, not a substitute for a full bucket policy review
        )
        s3_uri = f"s3://{BUCKET_NAME}/{key}"
        logger.info(f"Successfully uploaded PDF to S3: {s3_uri}")
        return s3_uri
    except Exception as e:
        logger.error(f"S3 upload operation failed: {redact_pii(str(e))}")
        raise e

def convert_floats(obj):
    if isinstance(obj, float):
        return Decimal(str(obj))
    elif isinstance(obj, dict):
        return {k: convert_floats(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [convert_floats(v) for v in obj]
    return obj

def convert_floats(obj):
    if isinstance(obj, float):
        return Decimal(str(obj))
    elif isinstance(obj, dict):
        return {k: convert_floats(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [convert_floats(v) for v in obj]
    return obj

def save_claim_stage(claim_id: str, stage: str, data: dict, status: str = None) -> dict:
    """Writes ONE audit record for ONE pipeline stage. Call this after
    EVERY stage completes (Intake, Policy Validation, Adjudication,
    Cross-Lens Reconciliation, Execution, Audit) — not just at the end.

    Args:
        claim_id: shared across every stage's record for this claim —
                  this is the partition key, so `get_claim_history()`
                  can retrieve every stage for this claim in one Query.
        stage: e.g. "INTAKE", "POLICY_VALIDATION", "ADJUDICATION",
               "CROSS_LENS_RECONCILIATION", "EXECUTION", "AUDIT" —
               becomes part of the sort key so records are naturally
               ordered chronologically when queried.
        data: whatever this stage produced — stored as-is (already
              structured JSON-safe from parse_json_response elsewhere)
        status: optional short status string for this specific stage
                (e.g. "SUCCESS", "FAILED") — distinct from the claim's
                overall routing_decision, which is a separate concept
                tracked in the EXECUTION stage record.

    Returns:
        The item that was written, for logging/debugging convenience.
    """
    if not CLAIMS_TABLE:
        raise ValueError("CLAIMS_TABLE environment variable is not set.")
    if not dynamodb:
        raise RuntimeError("AWS credentials or configuration is missing.")

    table = dynamodb.Table(CLAIMS_TABLE)
    timestamp = datetime.now(timezone.utc).isoformat()
    # Sort key format: "STAGE#ISO-TIMESTAMP" — sorts correctly as a
    # string AND is human-readable directly in the DynamoDB console,
    # without needing to decode anything.
    stage_timestamp = f"{stage}#{timestamp}"

    item = {
        "ClaimID": claim_id,
        "StageTimestamp": stage_timestamp,
        "Stage": stage,
        "Timestamp": timestamp,
        "Data": data,
    }
    #item = convert_floats(item)
    if status:
        item["StageStatus"] = status

    try:
        item = convert_floats(item)
        table.put_item(Item=item)
        logger.info(f"Saved stage record: claim={claim_id}, stage={stage}, status={status or 'n/a'}")
        return item
    except Exception as e:
        logger.error(f"DynamoDB put_item failed for claim {claim_id}, stage {stage}: {redact_pii(str(e))}")
        raise e


def get_claim_history(claim_id: str) -> list:
    """Reconstructs a claim's FULL stage-by-stage history from
    DynamoDB, in chronological order — this is the actual audit trail,
    queried directly, not assembled from logs. Used by the
    GET /claims/{claim_id} endpoint and for debugging."""
    if not CLAIMS_TABLE:
        raise ValueError("CLAIMS_TABLE environment variable is not set.")
    if not dynamodb:
        raise RuntimeError("AWS credentials or configuration is missing.")

    table = dynamodb.Table(CLAIMS_TABLE)
    response = table.query(
        KeyConditionExpression=boto3.dynamodb.conditions.Key("ClaimID").eq(claim_id)
    )
    items = response.get("Items", [])
    items.sort(key=lambda i: i.get("Timestamp", ""))
    return items


def save_notification_record(claim_id: str, notification: dict) -> dict:
    """Saves the Execution Agent's notification (see notifications.py)
    as its own stage record — this is what lets the UI show 'approval
    email sent' as a distinct, visible step in the claim's timeline,
    not just an invisible side effect."""
    return save_claim_stage(claim_id, "EXECUTION_NOTIFICATION", notification, status=notification.get("send_status"))
