# Compliance & Governance Guardrails
### Reference document for the Cross-Lens Reconciliation Agent, and the basis for guardrails.py's enforcement logic

This document describes the governance rules this pipeline is expected
to enforce. `guardrails.py` implements the parts that can be enforced
in code (PII/PHI redaction in logs, structural checks); the rest are
documented here so the reasoning agents — and anyone reviewing this
system — understand the full compliance posture, including what's
enforced automatically versus what still requires human judgment.

## PII / PHI Handling (HIPAA-relevant)

- **Never log full member IDs, SSNs, or dates of birth in plaintext**
  in application logs. `guardrails.py`'s `redact_pii()` function masks
  these before any `logger.info/warning/error` call touches claim
  content.
- **Never store raw uploaded PDF bytes anywhere except the designated
  encrypted S3 bucket.** Extracted text used for agent reasoning is
  transient (in-memory, per-request) and is not separately persisted
  outside the structured claim record.
- **DynamoDB records should store structured claim data, not raw
  document text**, for the same reason — minimize where PHI-bearing
  free text is duplicated across the system, so there are fewer places
  to secure and audit.

## Minimum Necessary Standard

Each agent in the pipeline should only receive the fields it actually
needs to do its job — this is why, for example, the Cross-Lens
Reconciliation Agent receives the Policy Validation and Adjudication
outputs (structured decisions) rather than the full raw claim text a
second time. Passing the minimum necessary data at each stage is a
HIPAA-aligned design principle, not just an efficiency choice.

## Human Oversight Requirements (the actual Responsible AI enforcement)

The following conditions must ALWAYS route to human review, with no
exception path and no way for a confidence score to override them:

1. Cross-lens disagreement detected (see `cross_lens.py`)
2. Fraud score at or above the configured threshold
3. Billed amount at or above the high-value review threshold ($5,000)
4. Any agent in the pipeline failing to return parseable output

These are **hard overrides** — see `cross_lens.py`'s
`reconcile()` function, where they are checked explicitly and cannot
be reasoned away by any other signal.

## Audit Trail Requirement

Every claim must have a reconstructable, stage-by-stage record of what
each agent saw and concluded — this is why `aws.py` writes one
DynamoDB item per pipeline stage (see `save_claim_stage()`) rather than
a single combined record at the end. A compliance reviewer should be
able to answer "why did this claim reach this outcome" from DynamoDB
alone, without needing to re-run the pipeline or consult application logs.

## What This Hackathon Build Does NOT Claim to Guarantee

Being explicit about this matters more than pretending otherwise:

- This is not a certified HIPAA-compliant system — proper certification
  requires a formal risk assessment, a signed Business Associate
  Agreement with AWS, encryption-at-rest verification across every
  service in use, and access-control audits beyond what a hackathon
  build implements.
- PII redaction here covers logs and is a defense-in-depth measure, not
  a substitute for encryption-at-rest/in-transit configuration on the
  underlying AWS services themselves (S3 SSE, DynamoDB encryption),
  which should also be enabled at the infrastructure level.
