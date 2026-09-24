"""
The Tribunal investigation loop.

  trigger -> open case -> prior belief
  loop: choose next evidence by VoI -> run it -> update belief -> record NBA
        -> stop when the decision is settled or nothing is worth its cost
  -> final NBA + approval route -> SAR if policy requires -> narrate -> write case to graph

The NBA is recorded TWICE (initial, before extra evidence; final, after) to satisfy the
submission format.
"""
from __future__ import annotations

from . import policy, voi
from .belief import Belief
from .evidence import CATALOG
from .graph_client import GraphClient
from .narrate import narrate
from .schemas import Case, EvidenceResult


def classify_typology(client: GraphClient, card1: int, final_prob: float) -> str:
    """
    Label the fraud from its diagnostic signals (separate from the block/clear DECISION).
    Runs cheap graph queries and picks the strongest pattern above its threshold.
    """
    sd = client.shared_device_count(card1)
    se = client.shared_email_count(card1)
    vel = client.velocity_24h(card1)
    mm = client.identity_mismatch_score(card1)
    candidates = []
    if sd >= 3:
        candidates.append(("account_farming", sd / 8.0))
    if se >= 2:
        candidates.append(("shared_email_ring", se / 6.0))
    if vel >= 8:
        candidates.append(("card_testing", vel / 16.0))
    if mm >= 0.4:
        candidates.append(("identity_mismatch", mm))
    if candidates:
        return max(candidates, key=lambda t: t[1])[0]
    return "none" if final_prob <= policy.CLEAR else "unknown"


def investigate(client: GraphClient, case_id: str, card1: int, trigger: str,
                on_step=None, max_steps: int = 8) -> Case:
    prior, exposure = client.prior_and_exposure(card1)
    case = Case(case_id=case_id, trigger=trigger, card1=card1,
                prior_prob=prior, exposure=exposure)
    belief = Belief(prior)

    # NBA #1: before gathering any additional evidence
    case.nba_initial = policy.decide(belief.prob, exposure, "unknown")

    used: set[str] = set()
    for _ in range(max_steps):
        unused = [e for e in CATALOG if e.key not in used]
        nxt, score, reason = voi.choose_next(unused, belief.logodds, belief.prob, exposure)
        if nxt is None:
            case.stop_reason = reason
            break

        prob_before = belief.prob
        raw = nxt.fetch(client, card1)
        bucket = nxt.bucketize(raw)
        lr = nxt.lr_for(bucket)
        prob_after = belief.update(lr)
        used.add(nxt.key)

        result = EvidenceResult(
            key=nxt.key, label=nxt.label, raw_value=raw, bucket=bucket,
            likelihood_ratio=lr, cost=nxt.cost,
            prob_before=prob_before, prob_after=prob_after, rationale=reason,
        )
        case.evidence.append(result)
        if nxt.key == "similar_cases" and isinstance(raw, list):
            case.similar_cases = raw
        if on_step:
            on_step(result, belief.prob)
    else:
        case.stop_reason = f"reached max {max_steps} evidence steps"

    case.final_prob = belief.prob
    case.typology = classify_typology(client, card1, case.final_prob)

    # NBA #2: final, after evidence
    case.nba_final = policy.decide(case.final_prob, exposure, case.typology)
    case.actions.append(case.nba_final)

    if policy.should_file_sar(case.final_prob, case.typology):
        case.sar = policy.build_sar(case)
        case.actions.append(policy.Action("file_sar", policy.APPROVAL["file_sar"],
                                           "policy: confidence + typology require a SAR"))

    case.narrative = narrate(case)
    client.write_case(case.to_dict())
    return case
