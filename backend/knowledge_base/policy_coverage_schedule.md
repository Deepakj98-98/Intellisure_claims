# Policy Coverage Schedule — Plan A
### Reference document for the Cross-Lens Reconciliation Agent and the Policy Validation Agent

This is the source of truth both agents should reason against. If either
agent's conclusion contradicts what's written here, that is exactly the
kind of contradiction the Cross-Lens Reconciliation Agent exists to catch.

| Procedure Code | Description | Covered | Copay | Coinsurance | Requires Prior Auth |
|---|---|---|---|---|---|
| 99213 | Office visit, established patient, low complexity | Yes | $25 | 20% | No |
| 99214 | Office visit, established patient, moderate complexity | Yes | $35 | 20% | No |
| 80053 | Comprehensive metabolic panel (lab) | Yes | $0 | 20% | No |
| 71046 | Chest X-ray, 2 views | Yes | $0 | 20% | No |
| 70551 | MRI, brain, without contrast | Yes | $0 | 20% | **Yes** |
| 27447 | Total knee arthroplasty (knee replacement) | Yes | $0 | 20% | **Yes** |
| 96413 | Chemotherapy administration, IV, first hour | Yes | $0 | 10% | **Yes** |
| 17000 | Cosmetic lesion removal | **No — excluded** | — | — | — |

## Notes for reasoning

- A procedure can be **covered** and still **require prior authorization**
  before payment — these are two independent facts. A claim can correctly
  show "covered: true" from a policy-fit perspective while still needing
  to be rejected or escalated because authorization wasn't obtained.
  **This is the single most common contradiction pattern the Cross-Lens
  Reconciliation Agent should watch for.**
- Exclusions (e.g., cosmetic procedures) are covered under no circumstance,
  regardless of medical necessity documentation.
- This schedule reflects Plan A only. A real deployment would look up the
  member's actual plan; this hackathon build assumes Plan A for every claim
  unless the claim document states otherwise.
