"""
notifications.py — The Execution Agent's action layer: sends the
member/provider a notification about the claim outcome.

WHAT THIS DOES:
After a routing decision is final (AUTO_APPROVED, NEEDS_HUMAN_REVIEW,
or MISSING_INFORMATION), this module constructs a notification —
approval confirmation, rejection notice, or "under review" notice —
and either sends it via Amazon SES (if configured) or, for hackathon
purposes, MOCKS the send and records exactly what would have been
sent. Either way, the notification record is saved to DynamoDB and
surfaced in the UI, which is the actual point: proving the pipeline
doesn't just decide, it ACTS on the decision.

WHY MOCK BY DEFAULT:
Setting up a verified SES sending domain/address is a real setup step
that depends on your specific AWS account's SES sandbox status —
not something to block your hackathon demo on. Set SES_ENABLED=true
and SES_SENDER_EMAIL in your environment to send real emails once
that's set up; until then, this logs and stores the notification
content as if it had been sent, which is sufficient to demonstrate
the execution layer end-to-end.
"""

import os
import logging
from datetime import datetime, timezone

import boto3

from guardrails import redact_pii

logger = logging.getLogger(__name__)

SES_ENABLED = os.getenv("SES_ENABLED", "false").lower() == "true"
SES_SENDER_EMAIL = os.getenv("SES_SENDER_EMAIL", "noreply@example.com")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")

try:
    ses_client = boto3.client("ses", region_name=AWS_REGION) if SES_ENABLED else None
except Exception as e:
    logger.warning(f"SES client could not be initialized (falling back to mock mode): {e}")
    ses_client = None


# ---------------------------------------------------------------------------
# Notification templates — deliberately simple, plain-language. A real
# deployment would use branded HTML templates; these are content-complete
# but visually minimal, appropriate for a hackathon demo.
# ---------------------------------------------------------------------------
def _build_notification_content(claim_id: str, routing_decision: str, claim_json: dict, reasons: list) -> dict:
    """Builds the notification's subject/body based on the final
    routing decision. Kept as pure content construction (no I/O) so
    it's independently testable without needing SES or DynamoDB."""
    procedure = claim_json.get("ProcedureCode", claim_json.get("procedure_code", "the billed procedure"))
    member_id = claim_json.get("MemberID", claim_json.get("member_id", "member"))

    if routing_decision == "AUTO_APPROVED":
        subject = f"Your claim {claim_id} has been approved"
        body = (
            f"Good news — your claim {claim_id} for procedure {procedure} has been reviewed "
            f"and approved. Payment processing will proceed automatically. No action is needed "
            f"from you at this time."
        )
    elif routing_decision == "NEEDS_HUMAN_REVIEW":
        subject = f"Your claim {claim_id} requires additional review"
        body = (
            f"Your claim {claim_id} for procedure {procedure} requires review by a claims "
            f"examiner before a final decision is made. This is a routine step for certain claim "
            f"types and does not necessarily mean anything is wrong with your submission. "
            f"You will be notified once the review is complete."
        )
    elif routing_decision == "MISSING_INFORMATION":
        subject = f"Action needed: claim {claim_id} is missing information"
        body = (
            f"We were unable to fully process your claim {claim_id} because some required "
            f"information could not be read from the submitted document. Please resubmit with "
            f"clearer documentation, or contact support for help."
        )
    else:
        subject = f"Update on your claim {claim_id}"
        body = f"Your claim {claim_id} status has been updated to: {routing_decision}."

    if reasons:
        # Internal-facing detail, appended for the DynamoDB/audit record
        # only — a real member-facing email would likely NOT include
        # this level of internal reasoning detail. Kept here because
        # it's useful for the demo/audit trail, flagged clearly as such.
        body += "\n\n[Internal reasoning detail, not typically member-facing]: " + " | ".join(reasons)

    return {"subject": subject, "body": body}


def send_claim_notification(claim_id: str, routing_decision: str, claim_json: dict, reasons: list, recipient_email: str = None) -> dict:
    """The Execution Agent's main action: send (or mock-send) the
    outcome notification, and return a record of what was sent for
    persistence to DynamoDB.

    Args:
        claim_id: the claim this notification is about
        routing_decision: "AUTO_APPROVED" | "NEEDS_HUMAN_REVIEW" | "MISSING_INFORMATION"
        claim_json: structured claim data (for personalizing the message)
        reasons: list of reasons from cross_lens.py, for the internal audit detail
        recipient_email: where to send it — defaults to a placeholder
                          since sample claims don't carry a real email
                          address; a production version would pull this
                          from the claim or member record

    Returns:
        A dict describing exactly what was (or would have been) sent —
        this is what gets saved to DynamoDB and shown in the UI.
    """
    recipient = recipient_email or "member-notifications@example.com"
    content = _build_notification_content(claim_id, routing_decision, claim_json, reasons)
    timestamp = datetime.now(timezone.utc).isoformat()

    if SES_ENABLED and ses_client:
        try:
            ses_client.send_email(
                Source=SES_SENDER_EMAIL,
                Destination={"ToAddresses": [recipient]},
                Message={
                    "Subject": {"Data": content["subject"]},
                    "Body": {"Text": {"Data": content["body"]}},
                },
            )
            send_status = "SENT_VIA_SES"
            logger.info(redact_pii(f"Notification for claim {claim_id} sent via SES to {recipient}"))
        except Exception as e:
            send_status = "SES_SEND_FAILED"
            logger.error(redact_pii(f"SES send failed for claim {claim_id}: {e}"))
    else:
        send_status = "MOCKED_NOT_SENT"
        logger.info(redact_pii(
            f"[MOCK NOTIFICATION] Claim {claim_id} -> {recipient} | Subject: {content['subject']}"
        ))

    return {
        "claim_id": claim_id,
        "recipient": recipient,
        "subject": content["subject"],
        "body": content["body"],
        "send_status": send_status,
        "sent_at": timestamp,
    }
