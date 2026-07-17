"""
cross_lens.py — The Cross-Lens Reconciliation Agent.

WHAT THIS AGENT DOES, AND WHY IT'S THE MOST IMPORTANT FILE IN THIS REPO:
Every other agent in this pipeline (Intake, Policy Validation,
Adjudication) reasons over ONE lens at a time. This agent's entire job
is reasoning ACROSS all of them at once, specifically looking for
contradictions a single-lens agent would miss — e.g., Policy
Validation correctly says "covered," Adjudication correctly says
"approved," and BOTH can be individually correct while still adding up
to a claim that should never be auto-approved, because prior
authorization is missing. This is the difference between a claims
pipeline and a claims REASONING system, and it is your single
strongest answer to "why is this agentic, not just four API calls in
a row."

WHY THIS IS BOTH A DETERMINISTIC CHECK *AND* AN OPTIONAL BEDROCK AGENT:
The hard-override rules (prior auth, fraud threshold, high-dollar
threshold) are checked deterministically in Python FIRST — fast,
reliable, and testable without any AWS dependency at all (see the
tests at the bottom of this file's companion test, or run this module
directly). If CROSS_LENS_AGENT_ID is configured, we ALSO ask a Bedrock
Agent to reason over the same data for subtler contradictions the
deterministic rules don't explicitly encode — e.g., a diagnosis code
that doesn't clinically match the procedure code, which is a pattern-
recognition judgment, not a lookup-table rule. Both results are
merged; a deterministic hard-override can never be overridden BACK by
the agent's opinion — that would defeat the entire point of a hard
override.

WHERE THE REASONING KNOWLEDGE COMES FROM:
knowledge_base/policy_coverage_schedule.md,
knowledge_base/prior_authorization_rules.md,
knowledge_base/fraud_watchlist.json — loaded once at import time (see
_load_knowledge_base below) and referenced by both the deterministic
checks and, if configured, included in the prompt sent to the Bedrock
agent so it reasons against the same source of truth, not its general
training knowledge.
"""

import json
import logging
import os
from pathlib import Path

from guardrails import redact_pii

logger = logging.getLogger(__name__)

KB_DIR = Path(__file__).resolve().parent / "knowledge_base"

CROSS_LENS_AGENT_ID = os.getenv("CROSS_LENS_AGENT_ID")     # optional — see module docstring
CROSS_LENS_AGENT_ALIAS = os.getenv("CROSS_LENS_AGENT_ALIAS")


def _load_knowledge_base() -> dict:
    """Loads the reference documents once, at import time — these
    don't change per-request, so re-reading them from disk on every
    claim would be wasteful. If a file is missing, this fails LOUDLY
    (raises) rather than silently reasoning with incomplete rules,
    since a missing prior-authorization ruleset is exactly the kind of
    gap that could let a claim wrongly auto-approve."""
    try:
        prior_auth_text = (KB_DIR / "prior_authorization_rules.md").read_text()
        policy_text = (KB_DIR / "policy_coverage_schedule.md").read_text()
        fraud_data = json.loads((KB_DIR / "fraud_watchlist.json").read_text())
    except FileNotFoundError as e:
        raise RuntimeError(
            f"Cross-Lens Reconciliation Agent cannot start: missing knowledge base file ({e}). "
            f"Check backend/knowledge_base/ contains all three reference files."
        ) from e

    # Parse the prior-auth-required procedure codes out of the markdown
    # table, so the deterministic check below can look them up by code
    # without re-parsing markdown on every claim.
    prior_auth_codes = set()
    for line in prior_auth_text.splitlines():
        if line.strip().startswith("|") and "Yes" in line:
            cols = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cols) >= 3 and cols[0].isdigit():
                prior_auth_codes.add(cols[0])

    return {
        "prior_auth_text": prior_auth_text,
        "policy_text": policy_text,
        "prior_auth_codes": prior_auth_codes,
        "fraud_watchlist": fraud_data.get("watchlist", {}),
        "high_value_threshold": fraud_data.get("high_value_threshold_usd", 5000),
        "fraud_score_threshold": fraud_data.get("fraud_score_review_threshold", 70),
    }


_KB = _load_knowledge_base()


def reconcile(claim_json: dict, policy_json: dict, adjudication_json: dict) -> dict:
    """The main entry point. Runs all deterministic hard-override
    checks, then (if configured) layers in a Bedrock agent's reasoning,
    and returns a single reconciliation verdict the rest of the
    pipeline acts on.

    Args:
        claim_json: Claim Intake Agent's structured output (member ID,
                    procedure code, billed amount, provider NPI, etc.)
        policy_json: Policy Validation Agent's structured output
                     (coverage determination)
        adjudication_json: Adjudication Agent's structured output
                            (approve/deny/exception decision)

    Returns:
        {
            "disagreement_found": bool,
            "disagreement_reasons": [str, ...],   # empty if none found
            "requires_human_review": bool,        # the actual hard-override signal
            "checks_performed": [str, ...],       # for the audit trail — what was actually checked
            "agent_commentary": str | None,       # only present if CROSS_LENS_AGENT_ID is configured
        }
    """
    reasons = []
    checks_performed = []

    procedure_code = str(claim_json.get("ProcedureCode", claim_json.get("procedure_code", ""))).strip()
    billed_amount = _safe_float(claim_json.get("BilledAmount", claim_json.get("billed_amount", 0)))
    provider_npi = str(claim_json.get("ProviderNPI", claim_json.get("provider_npi", ""))).strip()
    prior_auth_on_file = _coerce_bool(claim_json.get("PriorAuthOnFile", claim_json.get("prior_auth_on_file", False)))

    covered = _coerce_bool(policy_json.get("Covered", policy_json.get("covered", False)))
    adjudication_decision = str(adjudication_json.get("Decision", adjudication_json.get("decision", ""))).lower()

    # -----------------------------------------------------------------
    # HARD OVERRIDE 1: covered + prior-auth-required + not on file.
    # This is THE canonical contradiction this whole module exists to
    # catch — see knowledge_base/prior_authorization_rules.md.
    # -----------------------------------------------------------------
    checks_performed.append("prior_authorization_conflict")
    if covered and procedure_code in _KB["prior_auth_codes"] and not prior_auth_on_file:
        reasons.append(
            f"Policy Validation says procedure {procedure_code} is covered, and Adjudication "
            f"reached '{adjudication_decision}', but this procedure requires prior authorization "
            f"and none is on file. Coverage and compliance disagree — this cannot be auto-approved."
        )

    # -----------------------------------------------------------------
    # HARD OVERRIDE 2: fraud signal, independent of how clean the rest
    # of the claim looks. See knowledge_base/fraud_watchlist.json.
    # -----------------------------------------------------------------
    checks_performed.append("fraud_signal_check")
    fraud_entry = _KB["fraud_watchlist"].get(provider_npi)
    fraud_score = 5  # baseline
    if fraud_entry:
        fraud_score += fraud_entry.get("base_risk_score", 0)
        if billed_amount >= _KB["high_value_threshold"]:
            fraud_score += 15
        fraud_score = min(fraud_score, 100)
        if fraud_score >= _KB["fraud_score_threshold"]:
            reasons.append(
                f"Provider {provider_npi} has an elevated fraud score ({fraud_score}/100: "
                f"{fraud_entry.get('risk_reason', 'flagged provider')}) — routing to human review "
                f"regardless of adjudication outcome."
            )

    # -----------------------------------------------------------------
    # HARD OVERRIDE 3: high-dollar-value claims always get human
    # sign-off, independent of medical correctness. See
    # knowledge_base/prior_authorization_rules.md.
    # -----------------------------------------------------------------
    checks_performed.append("high_value_threshold_check")
    if billed_amount >= _KB["high_value_threshold"]:
        reasons.append(
            f"Billed amount ${billed_amount:,.2f} meets or exceeds the "
            f"${_KB['high_value_threshold']:,.2f} high-value review threshold — "
            f"requires human sign-off regardless of adjudication outcome."
        )

    # -----------------------------------------------------------------
    # HARD OVERRIDE 4: Adjudication itself flagged something ambiguous
    # (e.g. it returned "exception" rather than a clean approve/deny).
    # -----------------------------------------------------------------
    checks_performed.append("adjudication_ambiguity_check")
    if adjudication_decision in ("exception", "review", "pending", "unclear", ""):
        reasons.append(
            f"Adjudication Agent returned an ambiguous decision ('{adjudication_decision or 'empty'}') "
            f"rather than a clear approve/deny — routing to human review rather than guessing."
        )

    result = {
        "disagreement_found": len(reasons) > 0,
        "disagreement_reasons": reasons,
        "requires_human_review": len(reasons) > 0,
        "checks_performed": checks_performed,
        "agent_commentary": None,
    }

    # -----------------------------------------------------------------
    # OPTIONAL: layer in a real Bedrock Agent's reasoning for subtler
    # patterns the deterministic rules above don't explicitly encode
    # (e.g. diagnosis/procedure code clinical mismatch). This can ADD
    # a disagreement finding but can NEVER remove one the deterministic
    # checks above already found — see the module docstring for why.
    # -----------------------------------------------------------------
    if CROSS_LENS_AGENT_ID and CROSS_LENS_AGENT_ALIAS:
        try:
            agent_finding = _invoke_cross_lens_agent(claim_json, policy_json, adjudication_json)
            result["agent_commentary"] = agent_finding.get("commentary")
            if agent_finding.get("additional_disagreement_found"):
                result["disagreement_found"] = True
                result["requires_human_review"] = True
                result["disagreement_reasons"].append(
                    f"[Bedrock agent finding] {agent_finding.get('reason', 'Additional contradiction detected by reasoning agent.')}"
                )
        except Exception as e:
            # A failure in the OPTIONAL agent layer should never take
            # down the deterministic checks that already ran — log it
            # and proceed with what we have.
            logger.warning(f"Cross-Lens Bedrock agent call failed (deterministic checks still apply): {redact_pii(str(e))}")

    logger.info(
        f"Cross-Lens Reconciliation complete. disagreement_found={result['disagreement_found']}, "
        f"checks_performed={checks_performed}"
    )
    return result


def _invoke_cross_lens_agent(claim_json: dict, policy_json: dict, adjudication_json: dict) -> dict:
    """Calls an actual Bedrock Agent for the subtler, pattern-recognition
    half of cross-lens reasoning. Only called if CROSS_LENS_AGENT_ID is
    configured — see bedrock.py's invoke_agent() for the underlying
    boto3 call this wraps."""
    from bedrock import invoke_agent, parse_json_response

    prompt = (
        "You are the Cross-Lens Reconciliation Agent for CGI IntelliSure AI. "
        "Deterministic hard-override rules have already been checked separately — "
        "your job is to look for SUBTLER contradictions those rules don't cover, "
        "such as a diagnosis code that doesn't clinically support the billed procedure code, "
        "or an internal inconsistency between the claim, policy, and adjudication data below "
        "that a rule-based check wouldn't catch.\n\n"
        f"Claim: {json.dumps(claim_json)}\n"
        f"Policy Validation output: {json.dumps(policy_json)}\n"
        f"Adjudication output: {json.dumps(adjudication_json)}\n\n"
        f"Reference — policy coverage schedule:\n{_KB['policy_text']}\n\n"
        "Respond with ONLY this JSON shape:\n"
        '{"additional_disagreement_found": false, "reason": null, "commentary": "brief plain-language note"}'
    )

    raw = invoke_agent(CROSS_LENS_AGENT_ID, CROSS_LENS_AGENT_ALIAS, prompt)
    return parse_json_response(raw)


def _safe_float(value) -> float:
    try:
        return float(str(value).replace("$", "").replace(",", ""))
    except (ValueError, TypeError):
        return 0.0


def _coerce_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "yes", "1", "covered", "on file")
