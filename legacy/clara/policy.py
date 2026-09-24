"""
Deterministic policy: thresholds, action selection, approval routes, SAR trigger.

Decisions live HERE, not in the LLM. The agent may reason and narrate, but the
action it recommends is a pure function of (confidence, exposure, typology).
"""
from __future__ import annotations

from .schemas import Action, Case

# Confidence thresholds (see data/policy.md)
BLOCK_HARD = 0.85          # block/freeze allowed outright
BLOCK_SOFT = 0.70          # block allowed if exposure high
BLOCK_SOFT_EXPOSURE = 1000.0
CLEAR = 0.20               # allow
SAR_THRESHOLD = 0.85
SAR_TYPOLOGIES = {"account_farming", "shared_email_ring", "card_testing", "identity_mismatch"}

APPROVAL = {
    "allow": "auto",
    "monitor": "L1",
    "step_up": "auto",
    "request_validation": "L1",
    "warn_customer": "auto",
    "block_card": "L2",
    "freeze_account": "L2",
    "escalate": "L2",
    "file_sar": "L2",
}


def decide(prob: float, exposure: float, typology: str) -> Action:
    """Map current confidence + exposure to the recommended next action."""
    if prob >= BLOCK_HARD:
        name = "freeze_account" if exposure >= BLOCK_SOFT_EXPOSURE else "block_card"
        return Action(name, APPROVAL[name],
                      f"confidence {prob:.0%} >= {BLOCK_HARD:.0%} block threshold")
    if prob >= BLOCK_SOFT and exposure >= BLOCK_SOFT_EXPOSURE:
        return Action("block_card", APPROVAL["block_card"],
                      f"confidence {prob:.0%} >= {BLOCK_SOFT:.0%} and exposure "
                      f"${exposure:,.0f} >= ${BLOCK_SOFT_EXPOSURE:,.0f}")
    if prob <= CLEAR:
        return Action("allow", APPROVAL["allow"],
                      f"confidence {prob:.0%} <= {CLEAR:.0%} clear threshold")
    # uncertain zone
    if prob >= 0.5:
        return Action("monitor", APPROVAL["monitor"],
                      f"confidence {prob:.0%} elevated but below block threshold; monitor")
    return Action("warn_customer", APPROVAL["warn_customer"],
                  f"confidence {prob:.0%} in uncertain band; low-friction warning")


def should_file_sar(prob: float, typology: str) -> bool:
    return prob >= SAR_THRESHOLD and typology in SAR_TYPOLOGIES


def build_sar(case: Case) -> dict:
    """5W1H SAR draft, grounded in the gathered evidence."""
    top_ev = sorted(case.evidence, key=lambda e: abs(e.delta), reverse=True)[:3]
    return {
        "who": f"card1={case.card1}",
        "what": f"Suspected {case.typology.replace('_', ' ')}",
        "when": f"case {case.case_id}",
        "where": "card-not-present transactions in graph",
        "why": f"fraud confidence {case.final_prob:.0%} >= {SAR_THRESHOLD:.0%}",
        "how": "; ".join(f"{e.label}={e.raw_value} (LR {e.likelihood_ratio})" for e in top_ev),
        "confidence": round(case.final_prob, 3),
        "exposure_usd": round(case.exposure, 2),
    }
