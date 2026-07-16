"""
generate_sample_claims.py — Produces 5 sample claim PDFs, each
designed to exercise a DIFFERENT path through the pipeline (see
SAMPLE_CLAIMS_README.md in this same folder for exactly what output
to expect from each one).

Run this once to (re)generate the PDFs:
    python3 generate_sample_claims.py

Requires: pip install reportlab
"""

from pathlib import Path
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

OUT_DIR = Path(__file__).resolve().parent


def make_claim_pdf(filename: str, lines: list):
    path = OUT_DIR / filename
    c = canvas.Canvas(str(path), pagesize=letter)
    width, height = letter
    c.setFont("Helvetica-Bold", 13)
    c.drawString(72, height - 60, "HEALTH INSURANCE CLAIM FORM (CMS-1500 style — simulated)")
    c.setFont("Courier", 10)
    y = height - 90
    for line in lines:
        c.drawString(72, y, line)
        y -= 16
    c.save()
    print(f"Wrote {filename}")


# ---------------------------------------------------------------------------
# 1. CLEAN AUTO-APPROVE — routine office visit, fully covered, no prior
#    auth needed, provider not on any watchlist, low dollar amount.
#    EXPECTED: routing_decision = AUTO_APPROVED
# ---------------------------------------------------------------------------
make_claim_pdf("claim_1_auto_approve.pdf", [
    "Claim ID:              CLM-20001",
    "Member ID:             MEM-40210",
    "Provider NPI:          1205558812",
    "Provider Name:         Riverside Family Medicine",
    "State of Service:      CA",
    "Date of Service:       2026-05-14",
    "Diagnosis Code:        Z00.00 (Routine general medical exam)",
    "Procedure Code:        99213 (Office visit, established patient, low complexity)",
    "Billed Amount:         $150.00",
    "Prior Authorization on File:   N/A (not required for this procedure)",
    "",
    "Notes: Routine annual check-up. Standard in-network office visit,",
    "no unusual circumstances.",
])

# ---------------------------------------------------------------------------
# 2. MISSING INFORMATION — diagnosis code and provider NPI left blank,
#    simulating an incomplete/illegible submission.
#    EXPECTED: routing_decision = MISSING_INFORMATION (or, depending on
#    how the Intake Agent handles blanks, it may still extract partial
#    data and pass it forward with warnings — check the INTAKE stage's
#    StageStatus in DynamoDB: "SUCCESS_WITH_WARNINGS" indicates the
#    Intake Agent noticed the gap but proceeded anyway, which is worth
#    discussing in Q&A as a real edge case in LLM-based extraction
#    versus a hard deterministic gate).
# ---------------------------------------------------------------------------
make_claim_pdf("claim_2_missing_information.pdf", [
    "Claim ID:              CLM-20002",
    "Member ID:             MEM-40377",
    "Provider NPI:          [ILLEGIBLE ON ORIGINAL SCAN]",
    "Provider Name:         [ILLEGIBLE ON ORIGINAL SCAN]",
    "State of Service:      CA",
    "Date of Service:       2026-05-16",
    "Diagnosis Code:        [FIELD BLANK — NOT PROVIDED]",
    "Procedure Code:        71046 (Chest X-ray, 2 views)",
    "Billed Amount:         $140.00",
    "Prior Authorization on File:   N/A",
    "",
    "Notes: Scan quality poor on submitted fax. Provider NPI and",
    "diagnosis code fields did not transmit legibly.",
])

# ---------------------------------------------------------------------------
# 3. PRIOR-AUTH DISAGREEMENT — the centerpiece demo claim. MRI brain
#    (70551) is covered by the plan, but prior authorization is
#    explicitly NOT on file, and CA compliance rules require it for
#    this procedure. Policy Validation and Adjudication may both look
#    individually "correct" — Cross-Lens Reconciliation is what catches
#    the contradiction between them.
#    EXPECTED: routing_decision = NEEDS_HUMAN_REVIEW,
#    cross_lens.disagreement_found = true, reason mentions prior auth
# ---------------------------------------------------------------------------
make_claim_pdf("claim_3_prior_auth_disagreement.pdf", [
    "Claim ID:              CLM-20003",
    "Member ID:             MEM-40518",
    "Provider NPI:          1338820044",
    "Provider Name:         Pacific Neurology Associates",
    "State of Service:      CA",
    "Date of Service:       2026-05-18",
    "Diagnosis Code:        R51 (Headache, unspecified)",
    "Procedure Code:        70551 (MRI, brain, without contrast)",
    "Billed Amount:         $1,450.00",
    "Prior Authorization on File:   No",
    "",
    "Notes: Member's plan covers this procedure under standard benefits.",
    "California compliance rules require prior authorization for MRI",
    "brain imaging specifically, and none has been submitted or",
    "approved for this claim.",
])

# ---------------------------------------------------------------------------
# 4. FRAUD-FLAGGED PROVIDER — an otherwise routine, low-value office
#    visit, but the billing provider's NPI matches an entry in
#    knowledge_base/fraud_watchlist.json (base_risk_score: 85).
#    EXPECTED: routing_decision = NEEDS_HUMAN_REVIEW,
#    cross_lens.disagreement_found = true, reason mentions fraud score
# ---------------------------------------------------------------------------
make_claim_pdf("claim_4_fraud_flagged.pdf", [
    "Claim ID:              CLM-20004",
    "Member ID:             MEM-40629",
    "Provider NPI:          1999999992",
    "Provider Name:         Sunrise Outpatient Clinic",
    "State of Service:      CA",
    "Date of Service:       2026-05-20",
    "Diagnosis Code:        M54.5 (Low back pain)",
    "Procedure Code:        99214 (Office visit, established patient, moderate complexity)",
    "Billed Amount:         $340.00",
    "Prior Authorization on File:   N/A (not required for this procedure)",
    "",
    "Notes: Routine-looking claim. Billing provider has an elevated",
    "fraud risk score on file from prior claim pattern analysis.",
])

# ---------------------------------------------------------------------------
# 5. HIGH-DOLLAR-VALUE REVIEW — a legitimate knee replacement claim
#    WITH prior authorization properly on file (so this is NOT a
#    prior-auth disagreement) but the billed amount exceeds the
#    $5,000 high-value review threshold, which always requires human
#    sign-off regardless of how clean the rest of the claim looks.
#    EXPECTED: routing_decision = NEEDS_HUMAN_REVIEW,
#    cross_lens.disagreement_found = true, reason mentions high-value threshold
# ---------------------------------------------------------------------------
make_claim_pdf("claim_5_high_dollar_value.pdf", [
    "Claim ID:              CLM-20005",
    "Member ID:             MEM-40741",
    "Provider NPI:          1447792200",
    "Provider Name:         Bay Area Orthopedic Surgical Center",
    "State of Service:      CA",
    "Date of Service:       2026-05-22",
    "Diagnosis Code:        M17.11 (Unilateral primary osteoarthritis, right knee)",
    "Procedure Code:        27447 (Total knee arthroplasty)",
    "Billed Amount:         $8,500.00",
    "Prior Authorization on File:   Yes (Auth #PA-2026-88213, approved 2026-04-30)",
    "",
    "Notes: Fully authorized, medically necessary knee replacement.",
    "Billed amount exceeds the organization's high-value claim review",
    "threshold and requires examiner sign-off before payment,",
    "independent of the clean authorization and coverage status.",
])

print("\nAll 5 sample claims generated. See SAMPLE_CLAIMS_README.md for expected outcomes.")
