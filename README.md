# CGI IntelliSure AI — Claims Exception Resolution

Multi-agent claims processing platform built on Amazon Bedrock,
FastAPI, and React. Ingests a claim PDF, reasons across policy,
adjudication, and cross-lens contradiction checks, executes a
notification, and produces a complete, stage-by-stage audit trail.

**Start here:**
- **`backend/README.md`** — full architecture, updated workflow
  diagram, environment variables, all API endpoints, known limitations
- **`sample_claims/SAMPLE_CLAIMS_README.md`** — 5 ready-to-use sample
  claim PDFs, one per routing outcome, with expected results explained
- **`DEPLOYMENT.md`** — AWS infrastructure setup (DynamoDB, S3, Bedrock
  Agents, App Runner, Amplify)

## What's new in this version

Compared to the original build (Intake → Policy Validation →
Adjudication → Audit → Save, strictly linear):

1. **Cross-Lens Reconciliation** (`backend/cross_lens.py`) — the core
   differentiator. Checks Policy Validation and Adjudication's outputs
   TOGETHER for contradictions neither agent alone would catch (e.g.
   "covered" but prior-authorization is missing), plus fraud and
   high-dollar-value hard overrides. Deterministic and testable with
   zero AWS dependency, with an optional Bedrock agent layer for
   subtler pattern-recognition checks.
2. **Execution Agent** (`backend/notifications.py`) — sends (or mocks)
   an approval/rejection/review notification, so the pipeline visibly
   *acts* on its decision, not just decides.
3. **Governance guardrails** (`backend/guardrails.py`) — PII/PHI
   redaction before anything is logged, plus structural safety checks
   on agent output.
4. **Per-stage audit records, not one combined record per claim** — a
   claim that fails partway through still has every completed stage
   permanently saved (`backend/aws.py`'s `save_claim_stage`).
5. **FIFO batch processing** (`backend/queue_manager.py`) — multiple
   simultaneous PDF uploads are queued and processed in submission
   order, with a single claim's failure never blocking the ones queued
   behind it.
6. **Knowledge base** (`backend/knowledge_base/`) — the reference
   documents Cross-Lens Reconciliation (and, optionally, Policy
   Validation) reason against: policy coverage schedule, prior
   authorization rules, fraud watchlist.

See `backend/README.md` for the full updated architecture diagram and
reasoning behind each change.

## Quick start (local development)

This project deploys to AWS via **ECR + CodeBuild + ECS Express Mode
+ Amplify** (see `DEPLOYMENT.md`) — Docker is used there purely as the
CI/CD packaging format, not for local development. For local dev and
testing, use native Python/Node, which is faster to iterate in and is
what the **Dev Environment & Git Handbook** walks through step by step:

```bash
# Backend
cd backend
python3 -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py                                        # http://localhost:8000

# Frontend (separate terminal)
cd frontend
npm install
npm run dev                                           # http://localhost:5173
```

Then upload any file from `sample_claims/` — see
`sample_claims/SAMPLE_CLAIMS_README.md` for what to expect from each one.

*(An optional `docker-compose.yml` exists purely as a one-command
convenience if you'd rather not set up a venv — it is not the primary
workflow and is not used anywhere in the actual AWS deployment path.)*
