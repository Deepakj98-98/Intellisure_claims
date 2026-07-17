# Sample Claims — What Each One Tests, and What Output to Expect

Upload these through the frontend (or POST them to `/upload` directly)
to exercise every distinct path through the pipeline. Read this
BEFORE running them, so you know what "correct" looks like — if your
actual output doesn't match, that's a real signal to investigate, not
noise.

| File | Scenario | Expected `routing_decision` | What triggers it |
|---|---|---|---|
| `claim_1_auto_approve.pdf` | Clean, routine office visit | `AUTO_APPROVED` | Covered, no prior auth needed, provider not flagged, low dollar amount — no Cross-Lens checks fire |
| `claim_2_missing_information.pdf` | Illegible provider NPI, blank diagnosis code | `MISSING_INFORMATION` (or `NEEDS_HUMAN_REVIEW` with warnings — see note below) | Incomplete extraction — depends on how the Claim Intake Agent (an LLM) handles genuinely blank fields |
| `claim_3_prior_auth_disagreement.pdf` | MRI covered by policy, but prior auth NOT on file | `NEEDS_HUMAN_REVIEW` | Cross-Lens hard override #1 — coverage and compliance disagree |
| `claim_4_fraud_flagged.pdf` | Routine claim, but provider NPI is on the fraud watchlist | `NEEDS_HUMAN_REVIEW` | Cross-Lens hard override #2 — fraud score ≥ 70 |
| `claim_5_high_dollar_value.pdf` | Fully authorized knee replacement, billed $8,500 | `NEEDS_HUMAN_REVIEW` | Cross-Lens hard override #3 — amount ≥ $5,000 review threshold, independent of correctness |

## The one to center your demo on

**`claim_3_prior_auth_disagreement.pdf`** is your strongest example.
It's the one where a single-lens system would look correct at every
individual step — Policy Validation correctly says "covered,"
Adjudication correctly says "approved based on coverage" — and still
produces a wrong outcome, because neither one alone checks compliance
against the other's conclusion. Cross-Lens Reconciliation is what
catches it. Narrate this one slowly in your demo.

## A genuinely important note on `claim_2_missing_information.pdf`

Unlike the other four scenarios (which are governed by
`cross_lens.py`'s deterministic, always-reproducible rules), this
one's outcome depends on how the Claim Intake Agent — a real Bedrock
LLM call — handles a document with genuinely blank fields. It might:
- Return `null`/empty values for the missing fields, which
  `validate_agent_output_structure()` flags as a warning (check the
  `INTAKE` stage's `StageStatus` in DynamoDB for
  `SUCCESS_WITH_WARNINGS`), or
- Attempt to infer a plausible value anyway (an LLM's tendency,
  which the Intake Agent's prompt should explicitly discourage —
  see the "never guess" instruction pattern in this system's original
  agent design)

**This is worth testing for real and noting the actual behavior** —
if the Intake Agent invents a diagnosis code rather than reporting it
missing, that's a real, demonstrable finding about prompt design worth
mentioning in Q&A ("here's a case where we found the model guessing
when it shouldn't, and here's how we'd tighten the prompt to fix it")
— that kind of honest, specific observation reads as far more credible
than claiming everything works perfectly.

## Testing the FIFO batch behavior

Submit all 5 at once via `/upload/batch` (or the frontend's batch
upload, if wired up) and confirm:
1. They're all accepted immediately with `QUEUED` status
2. Polling `/claims/{claim_id}/status` for each shows them moving
   through `PROCESSING` in the order submitted (check timestamps in
   the logs — see `queue_manager.py`'s log lines for
   `[Worker 0] Starting claim ...`)
3. All 5 eventually reach a terminal status, and none are lost even
   if you set `QUEUE_WORKER_COUNT=1` (strict FIFO) vs. a higher value
