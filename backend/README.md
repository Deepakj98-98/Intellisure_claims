# IntelliSure AI: Claims Exception Resolution Platform MVP
### Updated architecture — Cross-Lens Reconciliation, Execution/Notification layer, FIFO batch processing, governance guardrails

IntelliSure AI is a multi-agent Insurance Claims Exception Resolution
platform using **Amazon Bedrock Agents** to orchestrate claim intake,
policy validation, decision adjudication, cross-lens contradiction
checking, action execution, and compliance auditing — with a complete,
stage-by-stage audit trail and FIFO handling of multiple simultaneous
submissions.

---

## Updated Workflow

```
                    Upload PDF(s)
                         │
                         ▼
              ┌─────────────────────┐
              │   FIFO Claim Queue    │  ← queue_manager.py. One or many
              │  (queue_manager.py)   │    files, processed in the order
              └──────────┬────────────┘    submitted. See "FIFO & Batch
                         │                 Processing" below.
                         ▼
              Extract PDF Text (pdf.py)
                         │
                         ▼
              Upload PDF to S3 (aws.py)
              [Server-side encrypted]
                         │
                         ▼
        ┌────────────────────────────────┐
        │      Claim Intake Agent          │──► DynamoDB stage record
        └────────────────┬─────────────────┘    (INTAKE)
                         │
                         ▼
        ┌────────────────────────────────┐
        │   Policy Validation Agent        │──► DynamoDB stage record
        └────────────────┬─────────────────┘    (POLICY_VALIDATION)
                         │
                         ▼
        ┌────────────────────────────────┐
        │      Adjudication Agent          │──► DynamoDB stage record
        └────────────────┬─────────────────┘    (ADJUDICATION)
                         │
                         ▼
        ┌────────────────────────────────┐
        │  Cross-Lens Reconciliation       │──► DynamoDB stage record
        │  (cross_lens.py) — THE           │    (CROSS_LENS_RECONCILIATION)
        │  differentiator. Checks          │
        │  Intake + Policy + Adjudication  │    Hard-override checks:
        │  TOGETHER for contradictions a    │    1. Prior-auth conflict
        │  single-lens agent would miss.    │    2. Fraud signal
        └────────────────┬─────────────────┘    3. High-dollar-value
                         │                       4. Adjudication ambiguity
                         ▼
        ┌────────────────────────────────┐
        │      Execution Agent             │──► DynamoDB stage record
        │      (notifications.py)          │    (EXECUTION_NOTIFICATION)
        │  Sends/mocks the outcome          │
        │  notification — the pipeline      │
        │  ACTS on the decision, not just    │
        │  decides.                          │
        └────────────────┬─────────────────┘
                         │
                         ▼
        ┌────────────────────────────────┐
        │        Audit Agent               │──► DynamoDB stage record
        │  Final compliance/QA pass over    │    (AUDIT)
        │  the COMPLETE trail, including     │
        │  any Cross-Lens override           │
        └────────────────┬─────────────────┘
                         │
                         ▼
              Return response to caller
     (claim_id + routing_decision + full stage results)
```

**Why Cross-Lens runs AFTER Adjudication, and Execution runs AFTER
Cross-Lens:** Cross-Lens needs Adjudication's actual decision to check
it against Policy Validation's — it can't run earlier. Execution needs
Cross-Lens's final verdict (including any override to human review)
before it knows what notification to send. Audit reviewing the whole
completed trail LAST means it's auditing the real final outcome, not
an intermediate decision that might get overridden a step later.

**Why per-stage DynamoDB records, not one combined record:** if a
claim fails partway through, everything up to that point is already
permanently saved — nothing is lost. See "Resilience & Audit Trail"
below.

---

## Cross-Lens Reconciliation — the core differentiator

Every other agent in this pipeline reasons over ONE lens at a time.
`cross_lens.py` is the one place that reasons across ALL of them at
once, specifically checking for contradictions a single-lens agent
would miss. Its four hard-override checks — each of which forces
`NEEDS_HUMAN_REVIEW` regardless of what any other agent concluded —
are:

1. **Prior-authorization conflict** — Policy Validation says a
   procedure is covered, but it requires prior authorization and none
   is on file. Coverage and compliance are two independent facts; a
   claim can be correctly "covered" and still not payable.
2. **Fraud signal** — the billing provider's NPI is on the fraud
   watchlist with a score at or above the configured threshold.
3. **High-dollar-value threshold** — billed amount ≥ $5,000 always
   requires human sign-off, independent of medical correctness.
4. **Adjudication ambiguity** — the Adjudication Agent itself returned
   something other than a clean approve/deny.

These checks run **deterministically in Python first** (fast,
reliable, zero AWS dependency, fully unit-testable — see the checks
performed in `cross_lens.reconcile()`), and **optionally layer in a
real Bedrock Agent call** for subtler pattern-recognition contradictions
(e.g., a diagnosis code that doesn't clinically support the billed
procedure) if `CROSS_LENS_AGENT_ID`/`CROSS_LENS_AGENT_ALIAS` are
configured. The deterministic hard overrides can never be reasoned
away by the optional agent's opinion — see `cross_lens.py`'s module
docstring for the full reasoning.

**Knowledge base:** `knowledge_base/policy_coverage_schedule.md`,
`knowledge_base/prior_authorization_rules.md`, and
`knowledge_base/fraud_watchlist.json` are the reference documents both
the deterministic checks and the optional Bedrock agent reason
against — not the model's general training knowledge.

---

## Governance & Guardrails (`guardrails.py`)

- **PII/PHI redaction**: `redact_pii()` masks SSNs, dates of birth,
  and member IDs before anything is logged — see
  `knowledge_base/compliance_guardrails.md` for the full governance
  rules this implements, including what's enforced in code versus
  what still requires organizational process.
- **Structural output validation**: `validate_agent_output_structure()`
  catches an agent returning unparseable or empty output early,
  before it's trusted by the next stage.
- **Honest scope note**: this is defense-in-depth for
  application-level logging, not a substitute for infrastructure-level
  encryption or a formal HIPAA certification process. Say this plainly
  if asked — see `compliance_guardrails.md`'s final section.

---

## Resilience & Audit Trail

Every stage writes its own DynamoDB item via `save_claim_stage()`
**immediately after that stage completes** — not batched and saved
only at the end. Partition key `ClaimID`, sort key
`Stage#ISO-Timestamp`. This means:

- A claim that fails at Adjudication still has its Intake and Policy
  Validation results permanently recorded.
- Every failure is saved with a **specific, human-readable reason**
  (never a bare stack trace) — check `GET /claims/{claim_id}` and read
  the last stage's `StageStatus` and `Data.error` field.
- `GET /claims/{claim_id}` reconstructs the complete stage-by-stage
  history with one DynamoDB Query — this is your real, queryable audit
  trail, not a slide claim.

---

## FIFO & Batch Processing (`queue_manager.py`)

**What happens with two or more PDFs at once:** every upload — whether
through `/upload` (single file) or `/upload/batch` (multiple files) —
goes through the same in-memory FIFO queue. A background worker
consumes claims **one at a time, in the exact order they were
submitted**, guaranteeing predictable ordering rather than a race
between concurrent requests.

Set `QUEUE_WORKER_COUNT` (default `1`) to run more than one worker
concurrently — with `1`, ordering is strictly FIFO; with more than
`1`, claims are still each processed correctly and nothing is lost,
but strict submission-order completion isn't guaranteed. Default is
`1` because predictability is more useful for a demo than raw
throughput at hackathon scale.

A single claim's processing failure **never** kills the worker loop —
claims queued behind a failure still process normally (verified with
an automated test; see the repo's test notes).

---

## Environment Variables

```env
# AWS Credentials and Region
AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=your-aws-access-key-id
AWS_SECRET_ACCESS_KEY=your-aws-secret-access-key

# Storage resources
BUCKET_NAME=your-s3-claims-bucket-name
CLAIMS_TABLE=your-dynamodb-claims-table-name

# Agent 1: Intake
CLAIM_AGENT_ID=your-claim-intake-agent-id
CLAIM_AGENT_ALIAS=your-claim-intake-agent-alias-id

# Agent 2: Policy Validation
POLICY_AGENT_ID=your-policy-validation-agent-id
POLICY_AGENT_ALIAS=your-policy-validation-agent-alias-id

# Agent 3: Adjudication
ADJUDICATION_AGENT_ID=your-adjudication-agent-id
ADJUDICATION_AGENT_ALIAS=your-adjudication-agent-alias-id

# Agent 4: Audit QA
AUDIT_AGENT_ID=your-audit-agent-id
AUDIT_AGENT_ALIAS=your-audit-agent-alias-id

# Cross-Lens Reconciliation — OPTIONAL. Deterministic hard-override
# checks work with ZERO configuration here. Only set these if you also
# want the optional Bedrock agent layer for subtler contradictions.
CROSS_LENS_AGENT_ID=
CROSS_LENS_AGENT_ALIAS=

# Execution / Notifications — OPTIONAL. Defaults to mock mode (logs +
# stores what WOULD have been sent) if SES_ENABLED is not "true".
SES_ENABLED=false
SES_SENDER_EMAIL=noreply@example.com

# FIFO queue — defaults to strict FIFO (1 worker)
QUEUE_WORKER_COUNT=1

# Frontend API URL configuration
VITE_API_URL=http://localhost:8000
```

---

## Running Locally

**Deployment target is AWS (ECR + CodeBuild + ECS Express Mode +
Amplify — see `DEPLOYMENT.md`), not Docker.** For local development,
use native Python/Node — faster iteration (no image rebuild per code
change), and it's exactly what the **Dev Environment & Git Handbook**
walks through in detail if you need the step-by-step version.

### Backend

```bash
cd backend
python3 -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```
Runs at `http://localhost:8000`. Set your `.env` file first (see
Environment Variables above) — without real AWS credentials and
Bedrock Agent IDs, `/health` will work but `/upload` will fail.

Quick sanity check:
```bash
curl http://localhost:8000/health
curl -X POST http://localhost:8000/upload -F "file=@../sample_claims/claim_1_auto_approve.pdf"
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```
Runs at `http://localhost:5173`. Set `VITE_API_URL=http://localhost:8000`
in `frontend/.env` if it isn't already pointing at your local backend.

### Optional: Docker Compose

A `docker-compose.yml` exists as a one-command convenience if you'd
rather not set up a venv — `docker-compose up --build` runs both
services. This is **not** used anywhere in the actual AWS deployment
path and is not the recommended day-to-day workflow; it's there purely
as a fallback if native setup is giving you trouble.

---

## API Endpoints

### `GET /health`
```json
{ "status": "healthy", "queue_depth": 0 }
```

### `POST /upload` — single claim PDF
Request: `multipart/form-data`, field `file` (must be `.pdf`).
Goes through the same FIFO queue as batch uploads, then awaits and
returns the result synchronously.

```json
{
  "claim_id": "CLAIM-A1B2C3D4E5F6",
  "routing_decision": "NEEDS_HUMAN_REVIEW",
  "claim": { "...": "..." },
  "policy": { "...": "..." },
  "decision": { "...": "..." },
  "cross_lens": {
    "disagreement_found": true,
    "disagreement_reasons": ["Policy Validation says procedure 70551 is covered, ... this cannot be auto-approved."],
    "requires_human_review": true,
    "checks_performed": ["prior_authorization_conflict", "fraud_signal_check", "high_value_threshold_check", "adjudication_ambiguity_check"]
  },
  "notification": {
    "recipient": "member-notifications@example.com",
    "subject": "Your claim CLAIM-A1B2C3D4E5F6 requires additional review",
    "send_status": "MOCKED_NOT_SENT"
  },
  "audit": { "...": "..." }
}
```

### `POST /upload/batch` — multiple claim PDFs at once
Request: `multipart/form-data`, field `files` (list of `.pdf` files).
Returns **immediately** with a `claim_id` per file and status
`QUEUED` — does not wait for processing. Poll the status endpoint
below for progress.

```json
{
  "submitted": [
    { "filename": "claim_1.pdf", "claim_id": "CLAIM-...", "status": "QUEUED" },
    { "filename": "claim_2.pdf", "claim_id": "CLAIM-...", "status": "QUEUED" }
  ],
  "queue_depth": 2
}
```

### `GET /claims/{claim_id}/status` — poll in-progress status
Reads from the in-memory queue state (fast, but does not survive a
backend restart — use the endpoint below for durable history).
```json
{ "claim_id": "CLAIM-...", "status": "PROCESSING" }
```

### `GET /claims/{claim_id}` — full stage-by-stage audit history
Reads directly from DynamoDB — durable, survives restarts, this is
your real audit trail.
```json
{
  "claim_id": "CLAIM-...",
  "stages": [
    { "Stage": "S3_UPLOAD", "StageStatus": "SUCCESS", "Timestamp": "..." },
    { "Stage": "INTAKE", "StageStatus": "SUCCESS", "Timestamp": "..." },
    { "Stage": "POLICY_VALIDATION", "StageStatus": "SUCCESS", "Timestamp": "..." },
    { "Stage": "ADJUDICATION", "StageStatus": "SUCCESS", "Timestamp": "..." },
    { "Stage": "CROSS_LENS_RECONCILIATION", "StageStatus": "DISAGREEMENT_FOUND", "Timestamp": "..." },
    { "Stage": "EXECUTION_NOTIFICATION", "StageStatus": "MOCKED_NOT_SENT", "Timestamp": "..." },
    { "Stage": "AUDIT", "StageStatus": "SUCCESS", "Timestamp": "..." }
  ]
}
```

---

## Sample Claims

See `/sample_claims/SAMPLE_CLAIMS_README.md` for 5 generated PDFs
covering every routing outcome (auto-approve, missing information,
prior-auth disagreement, fraud-flagged, high-dollar-value review),
including exactly what output to expect from each and which one to
center your demo on.

---

## Known Limitations (be upfront about these)

- **PDF only, text-extraction only** — no OCR fallback for
  scanned/image-only PDFs; `extract_pdf_text` will report empty text
  and the claim routes to `MISSING_INFORMATION`.
- **In-memory FIFO queue** — queue and status state do not survive a
  backend restart. DynamoDB stage records DO survive (that's your
  durable source of truth) — use `GET /claims/{claim_id}`, not
  `/status`, if you need to check after a restart.
- **Fraud scoring is a rules-based stub** against a small hardcoded
  watchlist, not a trained model — a deliberate MVP scope choice, not
  an oversight.
- **Not a certified HIPAA-compliant system** — see
  `knowledge_base/compliance_guardrails.md`'s final section for exactly
  what this build does and doesn't guarantee.
- **Single Plan A knowledge base** — the coverage schedule and
  compliance rules reflect one plan and one state (CA); a real
  deployment would look up the member's actual plan.

---

## Deployment Instructions

See `DEPLOYMENT.md` at the repo root for the full AWS deployment path —
CodeBuild building and pushing to ECR, ECS Express Mode (or App Runner
as a documented alternative) for the backend, and Amplify for the
frontend. The new modules (`cross_lens.py`, `notifications.py`,
`guardrails.py`, `queue_manager.py`) are plain Python files with no
additional infrastructure dependencies, so nothing about the
deployment steps changed because of them — just remember to add their
new optional environment variables (`CROSS_LENS_AGENT_ID`,
`SES_ENABLED`, etc.) to your service configuration if you use them.
