"""
Value-of-Information selection + stop rule -- Tribunal's differentiator.

Instead of running every check, the agent asks of each unused check:
  "Given what I believe right now, how likely is THIS check to actually change my
   decision (push me across the clear or block threshold)?"

  value(e) = P( running e moves the belief into a different decision category )

It then runs the most decisive CHEAP (graph) check first, escalates to a costly
controlled action (ask customer / step-up) only when no graph check can settle it,
and STOPS when:
  - the belief has already crossed a policy threshold ("enough to act"), or
  - nothing left could change the decision ("not worth the friction").

Both "why this evidence next" and "why stop" fall straight out of these numbers.
"""
from __future__ import annotations

import math

from . import policy
from .belief import prob_to_logodds
from .evidence import Evidence

FLOOR_P_CHANGE = 0.10   # a check must have >=10% chance to change the decision to be worth running


def _thresholds(exposure: float) -> tuple[float, float]:
    """Effective decision thresholds in log-odds (block relaxes when exposure is high)."""
    lo_clear = prob_to_logodds(policy.CLEAR)
    block_p = policy.BLOCK_SOFT if exposure >= policy.BLOCK_SOFT_EXPOSURE else policy.BLOCK_HARD
    lo_block = prob_to_logodds(block_p)
    return lo_clear, lo_block


def _category(lo: float, lo_clear: float, lo_block: float) -> str:
    if lo <= lo_clear:
        return "clear"
    if lo >= lo_block:
        return "block"
    return "undecided"


def p_change_decision(ev: Evidence, logodds: float, exposure: float) -> float:
    """Probability, over the evidence's outcome priors, that it flips the decision category."""
    lo_clear, lo_block = _thresholds(exposure)
    cur = _category(logodds, lo_clear, lo_block)
    p = 0.0
    for prob, bucket in ev.priors:
        new = logodds + math.log(max(ev.lr_for(bucket), 1e-6))
        if _category(new, lo_clear, lo_block) != cur:
            p += prob
    return p


def decided(prob: float, exposure: float) -> bool:
    if prob <= policy.CLEAR:
        return True
    if prob >= policy.BLOCK_HARD:
        return True
    if prob >= policy.BLOCK_SOFT and exposure >= policy.BLOCK_SOFT_EXPOSURE:
        return True
    return False


def choose_next(unused, logodds, prob, exposure):
    """Return (evidence, value, reason) for the next check, or (None, 0, reason) to STOP."""
    if decided(prob, exposure):
        return None, 0.0, (f"confidence {prob:.0%} has crossed a policy threshold "
                           f"(exposure ${exposure:,.0f}) - enough to act; further checks "
                           f"would add cost without changing the decision")
    scored = [(e, p_change_decision(e, logodds, exposure)) for e in unused]
    viable = [(e, p) for e, p in scored if p >= FLOOR_P_CHANGE]
    if not viable:
        return None, 0.0, ("no remaining check could realistically change the decision; "
                           "stopping rather than adding investigative friction")
    # graph queries before controlled actions; then most decisive; then cheapest
    viable.sort(key=lambda t: (t[0].is_action, -t[1], t[0].cost))
    best, p = viable[0]
    kind = "controlled action" if best.is_action else "graph query"
    reason = (f"{kind} '{best.key}' has the highest chance ({p:.0%}) of settling the "
              f"decision at cost {best.cost}")
    return best, p, reason
