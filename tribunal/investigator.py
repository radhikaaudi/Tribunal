"""
Tribunal investigator for the real HHGOA dataset.

Runs evidence probes, updates a log-odds belief (prior = a base rate nudged by the
risk score / customer report, NOT the risk score as a verdict), classifies the fraud
pattern, applies the Fraud Policy to produce the initial and final next-best-actions
(with a simulated customer/step-up response in between), files a SAR when the policy
calls for one, and assembles the exact answer JSON.
"""
from __future__ import annotations

import os
import time

import pandas as pd

from . import agent_llm
from . import graphrag
from . import policy_real as P
from . import signals as S
from .belief import Belief
from .memory import get_memory, signature

BASE_RATE = 0.25


def _risk_lr(score):
    if pd.isna(score):
        return 1.0
    return max(0.5, min(2.5, 1.0 + 2.0 * (float(score) - 0.5)))


def _card_id_for(ds, customer_id, fallback):
    return S.ds_cardid(ds, customer_id) if customer_id else fallback


def investigate(ds, case_row, on_step=None) -> dict:
    t0 = time.time()

    trajectory = []

    def step(label):
        trajectory.append({"stage": label, "confidence": round(belief.prob, 3)})
        if on_step:
            on_step(label, belief.prob)

    cust = case_row["customer_id"]
    flagged = ds.txn(case_row["flagged_txn_id"])
    hist = ds.customer_txns(cust)
    histsum = S.history_summary(ds, cust)
    trigger = case_row["trigger_type"]

    ctx = {
        "customer_id": cust, "card_id": case_row["card_id"],
        "flagged_txn_id": int(case_row["flagged_txn_id"]),
        "flagged": flagged, "hist": hist, "histsum": histsum,
        "trigger_type": trigger, "risk_score": case_row.get("risk_score"),
        "flagged_profile": (flagged["device_profile"] if flagged is not None
                            and flagged["device_profile"] else ""),
    }

    belief = Belief(BASE_RATE)
    evidence, fired = [], {}
    tool_calls = 0

    # ---- passive evidence: trigger signal ----
    if trigger == "risk_score":
        lr = _risk_lr(case_row.get("risk_score"))
        belief.update(lr)
        evidence.append({"claim": f"Bank model risk score {case_row.get('risk_score')} on the flagged "
                         f"transaction (a reason to look, not a verdict)", "source": "graph",
                         "ref": "field:risk_score", "entity_ids": [str(ctx["flagged_txn_id"])],
                         "side": "prosecution" if lr > 1 else "defense"})
        step("risk score")
    elif trigger == "customer_report":
        belief.update(5.0)   # a cardholder disputing their own charge is a strong signal (R2)
        evidence.append({"claim": "Cardholder reports they did not make the flagged purchase",
                         "source": "customer", "ref": "trigger:customer_report",
                         "entity_ids": [str(ctx["flagged_txn_id"])], "side": "prosecution"})
        step("customer report")
    elif trigger == "analyst_request":
        evidence.append({"claim": "Analyst flagged possible shared-device activity across cards",
                         "source": "analyst", "ref": "trigger:analyst_request",
                         "entity_ids": [str(ctx["flagged_txn_id"])], "side": "prosecution"})

    # ---- the two graph passes ----
    # PROSECUTOR: evidence supporting the fraud hypothesis (LR > 1)
    # DEFENDER:   legitimate explanations that contradict it   (LR < 1)
    prosecutor = [S.probe_flagged_anomaly, S.probe_card_testing, S.probe_cnp_burst,
                  S.probe_new_device, S.probe_out_of_region, S.probe_account_takeover,
                  S.probe_shared_device, S.probe_link_known_fraud]
    defender = [S.probe_recurring_match, S.probe_familiar_device, S.probe_familiar_merchant,
                S.probe_consistent_amount, S.probe_clean_tenure]
    if flagged is not None:
        for probe in prosecutor + defender:
            tool_calls += 1
            r = probe(ds, ctx)
            if r:
                fired[r["key"]] = r
                belief.update(r["lr"])
                side = "prosecution" if r["lr"] > 1 else "defense"
                evidence.append({"claim": r["claim"], "source": r["source"], "ref": r["ref"],
                                 "entity_ids": r["entity_ids"], "side": side})
                step(r["key"])

    p_graph = belief.prob

    # ---- pattern, connections, exposure ----
    pattern = _pick_pattern(fired, p_graph)
    shared = fired.get("shared_device")
    shared_origin = bool(shared) or "link_known_fraud" in fired
    connected_cards, connected_profiles = [], []
    if shared:
        connected_cards = shared["value"]["connected_cards"]
        connected_profiles = [shared["value"]["profile"]]
    elif fired.get("new_device") and ctx["flagged_profile"]:
        connected_profiles = [ctx["flagged_profile"]]

    affected = _affected_txns(ctx, fired, p_graph)
    exposure = float(hist[hist.TransactionID.isin([int(x) for x in affected])]["TransactionAmt"]
                     .abs().sum()) if affected else 0.0

    # ---- requested evidence (customer / step-up), simulated (challenge sec. 5) ----
    evidence_requests = []
    p_final = p_graph
    requested = False
    if trigger == "customer_report":
        # the cardholder has already disputed. Verify to separate a genuine denial (R2)
        # from a forgotten recurring charge (R7).
        requested = True
        recurring = "recurring_match" in fired
        if recurring:
            assumed = "On verification the cardholder recognises the charge as their own recurring payment"
            belief.update(0.15)
        else:
            assumed = "Cardholder reaffirms they did not make the purchase and still holds the card"
            belief.update(6.0)
        evidence_requests.append({"type": "customer_validation", "asked_after_step": len(evidence),
                                  "assumed_response": assumed})
        p_final = belief.prob
        evidence.append({"claim": assumed, "source": "customer", "ref": "evidence_request:1",
                         "entity_ids": [], "side": "defense" if recurring else "prosecution"})
        step("customer_validation")
    elif P.STOP_LOW < p_graph < P.STOP_HIGH:
        # R1: single/weak signal below 0.70 -> verify before acting
        requested = True
        denies = p_graph >= 0.5
        assumed = ("Customer states they did not make the transaction"
                   if denies else "Customer confirms they made the transaction")
        evidence_requests.append({"type": "customer_validation", "asked_after_step": len(evidence),
                                  "assumed_response": assumed})
        belief.update(8.0 if denies else 0.05)
        p_final = belief.prob
        evidence.append({"claim": assumed, "source": "customer", "ref": "evidence_request:1",
                         "entity_ids": [], "side": "prosecution" if denies else "defense"})
        step("customer_validation")

    # ---- verdict / status ----
    # policy 6: a fraud call needs >=2 independent inculpatory signals, else stay uncertain
    n_incul = sum(1 for r in fired.values() if r["lr"] > 1)
    n_incul += 1 if trigger == "customer_report" else 0
    n_incul += 1 if (requested and p_final > p_graph) else 0
    verdict = "fraud" if p_final >= 0.70 else "legitimate" if p_final <= 0.25 else "uncertain"
    if verdict == "fraud" and n_incul < 2:
        verdict = "uncertain"
    if verdict == "legitimate":
        pattern, affected, exposure = "none", [], 0.0
    # confirmed fraud with no distinctive pattern = generic card-not-present fraud (pattern 2)
    if verdict == "fraud" and pattern == "none":
        pattern = ("out_of_region_use" if "out_of_region" in fired
                   else "card_not_present_fraud")

    # --- Judge: did the fraud hypothesis SURVIVE its strongest defense? ---
    # actionable only if the prosecution outweighs the single best legitimate explanation
    strongest_defense = min([r["lr"] for r in fired.values() if r["lr"] < 1], default=1.0)
    hard_prosecution = any(r["lr"] >= 5 for r in fired.values())  # ring / link to known fraud
    survived = verdict == "fraud" and (p_final >= P.STOP_HIGH or hard_prosecution or strongest_defense > 0.4)
    if verdict == "fraud" and not survived:
        verdict = "uncertain"  # a strong legitimate explanation stands unrefuted -> don't block yet

    status = ("closed_fraud" if verdict == "fraud" and p_final >= P.STOP_HIGH else
              "closed_legitimate" if verdict == "legitimate" else "open")

    similar = ds.similar_prior_cases(pattern if pattern != "none" else "none", exposure)
    tool_calls += 1

    # ---- actions: initial (from p_graph) and final (from p_final) ----
    ct_cleared = bool(fired.get("card_testing") and fired["card_testing"]["value"]["cleared_over_100"])
    initial = _actions(trigger, _verdict_at(p_graph, pattern), p_graph, pattern, exposure,
                       shared_origin, ct_cleared, requested_stage=True)
    final = _actions(trigger, verdict, p_final, pattern, exposure, shared_origin, ct_cleared,
                     requested_stage=False)
    if status == "escalated" or any(a["action"] == "ESCALATE_TO_ANALYST" for a in final):
        status = "escalated"

    file_report = any(a["action"] == "FILE_REPORT" for a in final)
    sar = _build_sar(ds, ctx, verdict, p_final, pattern, exposure, affected, connected_cards,
                     connected_profiles, shared_origin, file_report)

    stop_reason = _stop_reason(p_final, verdict, len([e for e in evidence if e["source"] == "graph"]),
                               requested)
    what_changed = ("nothing" if not requested else
                    f"The simulated customer response moved confidence from {p_graph:.0%} to "
                    f"{p_final:.0%}, which changed the recommended action.")

    # --- the Tribunal verdict (two columns + conclusion), for the case record & UI ---
    if verdict == "fraud":
        conclusion = "The fraud hypothesis survived its strongest defense."
    elif verdict == "legitimate":
        conclusion = "A legitimate explanation accounts for the alert; the fraud hypothesis did not survive."
    else:
        conclusion = ("Prosecution and defence are both credible; the evidence is conflicting and a "
                      "decisive fact is missing.")
    debate = {
        "prosecution": [e["claim"] for e in evidence if e.get("side") == "prosecution"],
        "defense": [e["claim"] for e in evidence if e.get("side") == "defense"],
        "conclusion": conclusion,
        "missing_evidence": [er["type"] for er in evidence_requests],
    }

    graph_case_id = f"CASE-2016-{int(case_row['flagged_txn_id']) % 100000}"

    # ---- agentic layer: GraphRAG grounding + case memory + LLM reasoning ----
    decision = {"verdict": verdict, "fraud_probability": round(p_final, 3), "pattern": pattern}
    n_graph_ev = len([e for e in evidence if e["source"] == "graph"])
    mem = get_memory()
    sig = signature(pattern, exposure, n_graph_ev, shared_origin, p_final)
    mem_hits = mem.recall(sig, pattern, k=3, exclude=case_row["case_id"])
    tool_calls += 1
    grounding = graphrag.ground(ctx, evidence, pattern, fired, similar_cases=similar + mem_hits)
    tool_calls += 1
    reasoning = agent_llm.reason(ctx, evidence, decision, grounding)
    tool_calls += len(reasoning.get("tool_plan", []))

    device_profiles = [p for p in (connected_profiles + [ctx["flagged_profile"]]) if p]
    progression = _progression(case_row, ctx, graph_case_id, status, evidence,
                               evidence_requests, verdict, p_final, initial, final, trajectory)
    # write the resolved case back to memory (offline mirror) AND to the live graph
    # (a Case vertex via write_case.gsql) when a TigerGraph backend is configured.
    mem_record = {
        "case_id": case_row["case_id"], "kind": "resolved",
        "pattern": pattern, "outcome": verdict, "exposure_usd": round(exposure, 2),
        "connected_card_ids": connected_cards, "device_profiles": device_profiles,
        "customer_id": cust, "sig": sig,
        "note": _summary(ctx, verdict, pattern, p_final, exposure, shared_origin, similar)[:240],
    }
    mem.remember(mem_record)
    writeback = _graph_writeback(graph_case_id, {
        "case_id": graph_case_id, "card_id": ctx["card_id"], "verdict": verdict,
        "confidence": round(p_final, 3), "pattern": pattern, "exposure_usd": round(exposure, 2),
        "action": final[0]["action"] if final else "", "note": mem_record["note"],
    })

    return {
        "case_id": case_row["case_id"],
        "debate": debate,
        "agent_reasoning": reasoning,
        "graphrag": grounding,
        "case_progression": progression,
        "case_memory": {"retrieved": mem_hits, "recurring_entities": mem.recurring_entities(),
                        "memory_size": mem.size()},
        "belief_trajectory": trajectory,
        "case": {
            "status": status, "verdict": verdict, "fraud_probability": round(p_final, 3),
            "pattern": pattern,
            "pattern_description": _pattern_desc(pattern, fired) if pattern == "undocumented" else "",
            "affected_txn_ids": [str(x) for x in affected],
            "first_suspicious_txn_id": (str(_first_susp(ctx, affected)) if affected else ""),
            "connected_card_ids": connected_cards,
            "connected_device_profiles": connected_profiles,
            "exposure_usd": round(exposure, 2),
            "evidence": evidence,
            "similar_prior_cases": [m["case_id"] for m in similar],
            "summary": _summary(ctx, verdict, pattern, p_final, exposure, shared_origin, similar)
                       + " " + conclusion,
            "written_to_graph": True, "graph_case_id": graph_case_id,
            "graph_writeback": writeback,
        },
        "evidence_requests": evidence_requests,
        "next_best_actions": {"initial": initial, "final": final, "what_changed": what_changed},
        "sar": sar,
        "stop_reason": stop_reason,
        "tool_calls": tool_calls,
        "tokens": reasoning.get("tokens", 0),
        "latency_s": round(time.time() - t0, 3),
    }


# --------------------------------------------------------------------------- helpers
def _graph_writeback(case_id, record):
    """Write the resolved case to the knowledge graph. On a live TigerGraph backend
    this upserts a Case vertex (write_case.gsql); offline the case-memory store is the
    graph mirror, so this is a no-op that reports 'memory'."""
    backend = os.getenv("TRIBUNAL_GRAPH_BACKEND", "mock").lower()
    if not backend.startswith(("tiger", "mcp")):
        return "memory"
    try:
        from .graph_client import make_client
        make_client().write_case(record)
        return "tigergraph"
    except Exception as e:
        return f"pending:{type(e).__name__}"


def _progression(case_row, ctx, graph_case_id, status, evidence, evidence_requests,
                 verdict, p_final, initial, final, trajectory):
    """Explicit case creation + progression record: an auditable timeline of how the
    case opened, what evidence was added, when evidence was requested, how the
    recommendation evolved, and the resolved status (challenge: create & progress a case)."""
    flagged = ctx.get("flagged")
    opened_at = ""
    try:
        if flagged is not None and flagged.get("ts") is not None:
            opened_at = str(flagged["ts"])
    except Exception:
        opened_at = ""

    seq = 0
    timeline = []

    def ev(event, actor, detail, confidence=None):
        nonlocal seq
        seq += 1
        timeline.append({"seq": seq, "event": event, "actor": actor,
                         "detail": detail, "confidence": confidence})

    ev("case_opened", "agent", f"Case {graph_case_id} opened on trigger '{ctx.get('trigger_type')}' "
       f"for card {ctx.get('card_id')}.", trajectory[0]["confidence"] if trajectory else None)
    for e in evidence:
        ev("evidence_added", e.get("source", "graph"),
           f"[{e.get('side', '')}] {e['claim']}", None)
    for er in evidence_requests:
        ev("evidence_requested", "agent",
           f"Requested {er['type']} (after step {er.get('asked_after_step')}): "
           f"{er.get('assumed_response', '')}", None)
    ev("assessed", "agent", f"Assessed {verdict} at {p_final:.0%}.", round(p_final, 3))
    ev("recommendation_initial", "agent",
       "Initial next-best-action: " + " | ".join(f"{a['action']}({a['route']})" for a in initial))
    ev("recommendation_final", "agent",
       "Final next-best-action: " + " | ".join(f"{a['action']}({a['route']})" for a in final))
    ev("case_" + status, "agent", f"Case resolved to status '{status}'.", round(p_final, 3))

    status_history = ["OPEN"]
    if evidence_requests:
        status_history.append("EVIDENCE_REQUESTED")
    status_history.append(status.upper())

    return {
        "case_id": graph_case_id,
        "opened_at": opened_at,
        "status": status,
        "status_history": status_history,
        "resolved": status not in ("open", "escalated"),
        "n_events": len(timeline),
        "timeline": timeline,
        "belief_trajectory": trajectory,
        "written_to_graph": True,
    }


def _pick_pattern(fired, p_graph):
    if "card_testing" in fired:
        return "card_testing"
    if "out_of_region" in fired:
        return "out_of_region_use"
    if "account_takeover" in fired:
        return "account_takeover"
    # an actual purchase anomaly (amount/product off, or a burst) - not just a New device
    anomaly = ("flagged_anomaly" in fired and fired["flagged_anomaly"]["lr"] > 1) or "cnp_burst" in fired
    if "new_device" in fired and anomaly:
        return "card_not_present_new_device"
    if anomaly:
        return "card_not_present_fraud"
    if "shared_device" in fired and fired["shared_device"]["value"]["n"] >= 3:
        return "undocumented"
    return "none"


def _verdict_at(p, pattern):
    return "fraud" if p >= 0.70 else "legitimate" if p <= 0.25 else "uncertain"


def _actions(trigger, verdict, prob, pattern, exposure, shared_origin, ct_cleared, requested_stage):
    A = []
    if verdict == "legitimate":
        if trigger == "customer_report":
            A += [P.act("CREATE_CASE", exposure, "R7: dispute logged for the record"),
                  P.act("VERIFY_WITH_CUSTOMER", exposure, "R7: confirm the recurring charge with the cardholder"),
                  P.act("WARN_CUSTOMER", exposure, "R7: charge matches the cardholder's recurring pattern")]
        else:
            A += [P.act("ALLOW_TRANSACTION", exposure, "evidence does not support fraud"),
                  P.act("CLOSE_NO_FRAUD", exposure, "R3: cleared as legitimate")]
        return A
    if verdict == "uncertain":
        if requested_stage:
            A.append(P.act("VERIFY_WITH_CUSTOMER", exposure, "R1: single/weak signal below 0.70, verify before blocking"))
        if prob >= P.CREATE_CASE_PROB or requested_stage or trigger == "customer_report":
            A.append(P.act("CREATE_CASE", exposure, "3a: open a case on a dispute / evidence request / prob >= 0.30"))
        if exposure > P.ESCALATE_EXPOSURE:
            A.append(P.act("ESCALATE_TO_ANALYST", exposure, "R8: uncertain and exposed over $500"))
        return A or [P.act("MONITOR_CARD", exposure, "insufficient evidence; raise monitoring")]
    # verdict == fraud
    if pattern == "card_testing":
        A.append(P.act("DECLINE_TRANSACTION", exposure, "R5: card-testing sequence observed"))
        A.append(P.act("BLOCK_CARD" if ct_cleared else "STEP_UP_AUTH", exposure,
                       "R5: purchase over $100 already cleared" if ct_cleared else "R5: step-up before further use"))
    else:
        A.append(P.act("BLOCK_CARD", exposure, "R2: unauthorized use confirmed"))
    A.append(P.act("CREATE_CASE", exposure, "R2/3a: open and record the case"))
    if pattern == "undocumented":
        A.append(P.act("ESCALATE_TO_ANALYST", exposure, "R9: coordinated abuse fitting no known pattern"))
    if P.should_file_report(verdict, prob, exposure, shared_origin, pattern):
        A.append(P.act("FILE_REPORT", exposure, "R2/R6: confirmed fraud meeting SAR criteria"))
    if shared_origin:
        A.append(P.act("MONITOR_CONNECTED_CARDS", exposure, "R6: monitor cards sharing the origin"))
    return A


def _affected_txns(ctx, fired, p_graph):
    if "card_testing" in fired:
        return [int(x) for x in fired["card_testing"]["entity_ids"]]
    if "cnp_burst" in fired:
        return [int(x) for x in fired["cnp_burst"]["entity_ids"]]
    return [ctx["flagged_txn_id"]]


def _first_susp(ctx, affected):
    h = ctx["hist"]
    sub = h[h.TransactionID.isin([int(x) for x in affected])]
    if sub.empty:
        return ctx["flagged_txn_id"]
    return int(sub.sort_values("ts").iloc[0]["TransactionID"])


def _stop_reason(prob, verdict, n_graph_evidence, requested):
    if prob >= P.STOP_HIGH:
        return (f"Fraud probability {prob:.0%} at/above 0.85 with {n_graph_evidence} independent "
                f"graph evidence items; further steps would not change the decision (policy 6).")
    if prob <= P.STOP_LOW:
        return f"Fraud probability {prob:.0%} at/below 0.15; cleared as legitimate (policy 6)."
    if requested:
        return "A verification response settled the question (policy 6)."
    return f"Probability {prob:.0%} remains uncertain; escalated per R8 rather than over-investigating."


def _summary(ctx, verdict, pattern, prob, exposure, shared, similar):
    sc = similar[0]["case_id"] if similar else "none"
    base = (f"Card {ctx['card_id']} (customer {ctx['customer_id']}), trigger {ctx['trigger_type']}. "
            f"Assessed {verdict} at {prob:.0%} confidence")
    if verdict == "fraud":
        base += (f", pattern {pattern}, exposure ${exposure:,.2f}"
                 + (", linked to other cards by shared origin" if shared else "")
                 + f". Nearest prior case: {sc}.")
    else:
        base += ". Evidence did not support fraud; recommended to close/monitor per policy."
    return base


def _pattern_desc(pattern, fired):
    if "shared_device" in fired:
        n = fired["shared_device"]["value"]["n"]
        return (f"Coordinated abuse: a single device profile is shared across {n} cards in a short "
                f"window, none matching the five documented patterns. Found by device-neighbour "
                f"traversal from the flagged transaction.")
    return "Activity fitting none of the five documented patterns."


def _build_sar(ds, ctx, verdict, prob, pattern, exposure, affected, connected_cards,
               connected_profiles, shared_origin, file_report):
    if not file_report:
        return {"file": False, "reason": _sar_reason_no(verdict, prob, exposure, shared_origin),
                "narrative": "", "subjects": [], "total_amount_usd": 0, "activity_dates": []}
    from .narrate_real import sar_narrative
    sub = ds.customer_txns(ctx["customer_id"])
    aff = sub[sub.TransactionID.isin([int(x) for x in affected])]
    dates = ([str(aff["ts"].min().date()), str(aff["ts"].max().date())]
             if not aff.empty else [])
    subjects = [ctx["customer_id"], ctx["card_id"]] + connected_cards[:8] + connected_profiles[:2]
    return {
        "file": True,
        "reason": f"R2/R6: {verdict} {pattern} with exposure ${exposure:,.2f}"
                  + (" and shared-origin links to other cards" if shared_origin else ""),
        "narrative": sar_narrative(ctx, pattern, exposure, aff, connected_cards, connected_profiles, dates),
        "subjects": subjects,
        "total_amount_usd": round(exposure, 2),
        "activity_dates": dates,
    }


def _sar_reason_no(verdict, prob, exposure, shared):
    if verdict != "fraud":
        return "No report: activity not confirmed or strongly suspected as fraud."
    if exposure <= P.SAR_EXPOSURE and not shared:
        return f"No report: confirmed fraud but exposure ${exposure:,.2f} is under $1,000 and no shared origin (3a)."
    return "No report."
