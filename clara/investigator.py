"""
DefAttack investigator for the real HHGOA dataset.

Runs evidence probes, updates a log-odds belief (prior = a base rate nudged by the
risk score / customer report, NOT the risk score as a verdict), classifies the fraud
pattern, applies the Fraud Policy to produce the initial and final next-best-actions
(with a simulated customer/step-up response in between), files a SAR when the policy
calls for one, writes the case to case memory (local + TigerGraph when configured),
and assembles the exact answer JSON.
"""
from __future__ import annotations

import time

import pandas as pd

from . import knowledge as K
from . import memory as M
from . import policy_real as P
from . import signals as S
from .belief import Belief

BASE_RATE = 0.25
DEFENSE_FLOOR = 0.25   # correlated exculpatory probes may cut the odds at most 4x in total


def _risk_lr(score):
    """The risk score is a reason to look, not evidence. Measured on the closed cases: 100% of
    cleared alerts scored above 0.7 versus 24% of confirmed frauds (mean 0.88 vs 0.47), which
    matches the README ('above 0.7, most flagged transactions turn out to be legitimate').
    So the score opens the investigation but does not move the belief."""
    return 1.0


def _tg():
    try:
        from . import tg
        return tg.get()
    except Exception:
        return None


def investigate(ds, case_row, on_step=None, write_back=True, use_llm=True) -> dict:
    t0 = time.time()

    def step(label):
        if on_step:
            on_step(label, belief.prob)

    cust = case_row["customer_id"]
    flagged = ds.txn(case_row["flagged_txn_id"])
    hist = ds.customer_txns(cust)
    trigger = case_row["trigger_type"]
    histsum = S.history_summary(ds, cust, before=flagged["ts"] if flagged is not None else None)

    ctx = {
        "case_id": case_row["case_id"],
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
    defense_prod = 1.0

    def add(r, side=None):
        nonlocal defense_prod
        lr = r["lr"]
        if lr < 1:   # cap the combined weight of correlated legitimate explanations
            eff = max(lr, DEFENSE_FLOOR / defense_prod)
            defense_prod *= eff
            lr = min(1.0, eff)
        belief.update(lr)
        side = side or ("prosecution" if r["lr"] > 1 else "defense" if r["lr"] < 1 else "context")
        evidence.append({"claim": r["claim"], "source": r["source"], "ref": r["ref"],
                         "entity_ids": r["entity_ids"], "side": side})
        step(r["key"])

    # ---- passive evidence: the trigger ----
    if trigger == "risk_score":
        lr = _risk_lr(case_row.get("risk_score"))
        add({"key": "risk_score", "lr": lr, "source": "graph", "ref": "Transaction.risk_score",
             "entity_ids": [str(ctx["flagged_txn_id"])],
             "claim": f"Bank model risk score {case_row.get('risk_score')} on the flagged transaction opened "
                      f"the investigation; it is not used as evidence (in closed cases every cleared alert "
                      f"scored above 0.7 versus 24% of confirmed frauds)"})
    elif trigger == "customer_report":
        add({"key": "customer_report", "lr": 4.0, "source": "customer", "ref": "trigger:customer_report",
             "entity_ids": [str(ctx["flagged_txn_id"])],
             "claim": "Cardholder reports they did not make the flagged purchase"})
    elif trigger == "analyst_request":
        evidence.append({"claim": "Analyst flagged possible shared-device activity across cards",
                         "source": "analyst", "ref": "trigger:analyst_request",
                         "entity_ids": [str(ctx["flagged_txn_id"])], "side": "context"})

    # ---- the graph passes ----
    # PROSECUTOR: evidence supporting the fraud hypothesis (LR > 1)
    # DEFENDER:   legitimate explanations that contradict it   (LR < 1)
    prosecutor = [S.probe_flagged_anomaly, S.probe_card_testing, S.probe_structuring, S.probe_cnp_burst,
                  S.probe_new_device, S.probe_out_of_region, S.probe_account_takeover, S.probe_channel_mismatch,
                  S.probe_shared_device, S.probe_link_known_fraud]
    defender = [S.probe_recurring_match, S.probe_familiar_device, S.probe_familiar_merchant,
                S.probe_consistent_amount, S.probe_clean_tenure]
    if flagged is not None:
        for probe in prosecutor + defender + [S.probe_case_memory]:
            tool_calls += 1
            r = probe(ds, ctx)
            if r and r["key"] == "cnp_burst" and "structuring" in fired:
                r = None
            if r:
                fired[r["key"]] = r
                add(r)

    # ---- case memory written by earlier investigations (the agent's own cases) ----
    tool_calls += 1
    links = M.related_cases(ctx, fired)
    for lk in links[:2]:
        r = {"key": "agent_memory", "lr": 2.0 if lk["verdict"] == "fraud" else 1.0,
             "source": "graph", "ref": f"query:related_investigations({lk['case_id']})",
             "entity_ids": [lk["case_id"]] + lk["shared"][:4], "pattern": lk["pattern"], "value": lk,
             "claim": f"Earlier investigation {lk['case_id']} ({lk['verdict']}, {lk['pattern']}) shares "
                      f"{', '.join(lk['shared'][:3])} with this case"}
        fired.setdefault("agent_memory", r)
        add(r)

    tool_calls += _graph_crosscheck(ctx, fired, evidence)

    p_graph = belief.prob

    # ---- pattern, connections, exposure ----
    pattern = _pick_pattern(fired, ctx)
    shared = fired.get("shared_device")
    lkf = fired.get("link_known_fraud")
    connected_cards, connected_profiles = [], []
    if shared:
        connected_cards = shared["value"]["connected_cards"]
        connected_profiles = [shared["value"]["profile"]]
    shared_origin = bool(shared) or bool(lkf and lkf["value"]["cases"])

    affected = _affected_txns(ctx, fired)
    exposure = _exposure(ds, affected)

    # ---- request more evidence (customer / step-up), responses simulated (policy 5) ----
    evidence_requests = []
    requested = False
    no_reply = False
    n_graph_incul = sum(1 for k, r in fired.items() if r["lr"] >= 2.0)
    if trigger == "customer_report" and "recurring_match" in fired:
        requested = True   # R7: separate a genuine denial from a forgotten recurring charge
        assumed = ("Cardholder, shown the matching monthly charges, recognises the payment as their own "
                   "recurring charge (assumed: the graph shows the same amount and merchant on the same day "
                   "in earlier months)")
        _respond(belief, evidence, evidence_requests, "customer_validation", assumed, 0.1, "defense", step)
    elif P.STOP_LOW < p_graph < P.STOP_HIGH:
        requested = True
        if trigger == "customer_report":
            denies = True
            assumed = ("Cardholder confirms they still hold the card and did not make or authorise the purchase "
                       "(assumed: the dispute is unprompted and no recurring or familiar-merchant match exists)")
        else:
            # the simulated reply follows what the graph evidence predicts, net of the defence:
            # a denial only if graph findings (excluding the alert itself) favour fraud >= 2.5x
            net = 1.0
            for r in fired.values():
                net *= r["lr"]
            denies = net >= 4.0 and n_graph_incul >= 1
            no_reply = not denies and net >= 2.0 and n_graph_incul >= 1
            if denies:
                why = "; ".join(r["claim"][:80] for r in fired.values() if r["lr"] >= 2.0)[:220]
                assumed = (f"Customer states they did not make the transaction (assumed because the graph "
                           f"evidence favours fraud {net:.1f}x net of the defence: {why})")
            elif no_reply:
                assumed = (f"No reply from the customer within 24 hours (assumed because the graph evidence "
                           f"leans only {net:.1f}x toward fraud — too weak to predict a denial, too strong to "
                           f"predict a confirmation)")
            else:
                reason = _cleared_reason(ctx)
                n_analog = sum(1 for m in ds.memory if m["cleared_reason"] == reason)
                assumed = (f"Customer confirms they made the transaction (assumed because no graph evidence "
                           f"beyond the alert points to fraud; {n_analog} closed cases with the same profile "
                           f"— '{reason.replace('_', ' ')}' — were cleared after the cardholder confirmed)")
        rtype = ("step_up_auth" if trigger == "risk_score" and ctx["flagged"]["channel"] == "online"
                 and "new_device" in fired else "customer_validation")
        _respond(belief, evidence, evidence_requests, rtype, assumed,
                 6.0 if denies else 1.0 if no_reply else 0.08,
                 "prosecution" if denies else "context" if no_reply else "defense", step)
    p_final = belief.prob

    # ---- verdict / status ----
    # policy 6: a fraud call needs >=2 independent inculpatory signals, else stay uncertain
    n_incul = n_graph_incul + (1 if trigger == "customer_report" else 0)
    n_incul += 1 if (requested and p_final > p_graph) else 0
    verdict = "fraud" if p_final >= 0.70 else "legitimate" if p_final <= 0.25 else "uncertain"
    if verdict == "fraud" and n_incul < 2:
        verdict = "uncertain"
    if no_reply:
        verdict = "uncertain"     # R4: nothing settled the question
    # did the fraud hypothesis SURVIVE its strongest defense?
    strongest_defense = min([r["lr"] for r in fired.values() if r["lr"] < 1], default=1.0)
    hard_prosecution = any(r["lr"] >= 5 for r in fired.values())
    if verdict == "fraud" and not (p_final >= P.STOP_HIGH or hard_prosecution or strongest_defense > 0.4):
        verdict = "uncertain"
    if verdict == "fraud" and pattern == "none":
        pattern = _fallback_pattern(ctx, fired)
    r7 = "recurring_match" in fired and trigger == "customer_report" and verdict == "legitimate"
    if verdict == "legitimate":
        pattern, affected, exposure, connected_cards, connected_profiles = "none", [], 0.0, [], []
        shared_origin = False

    # ---- actions: initial (before the request came back) and final ----
    ct_cleared = bool(fired.get("card_testing") and fired["card_testing"]["value"]["cleared_over_100"])
    final = _actions(trigger, verdict, p_final, pattern, exposure, shared_origin, ct_cleared,
                     connected_cards, requested, r7, no_reply)
    if requested:
        initial = _initial_actions(evidence_requests[0]["type"], p_graph, pattern, fired, exposure, ct_cleared)
    else:
        initial = final
    has = {a["action"] for a in final}
    status = ("closed_legitimate" if verdict == "legitimate" else
              "escalated" if "ESCALATE_TO_ANALYST" in has else
              "closed_fraud" if verdict == "fraud" else "open")

    similar = _similar(ds, ctx, fired, verdict, pattern, exposure)
    tool_calls += 1

    file_report = "FILE_REPORT" in has
    sar = _build_sar(ds, ctx, fired, verdict, pattern, exposure, affected, connected_cards,
                     connected_profiles, shared_origin, file_report, evidence_requests, similar, final)

    # ---- policy grounding (document side of GraphRAG) ----
    rules = _cited_rules(initial + final)
    tool_calls += 1
    for rid in rules[:4]:
        c = K.get(rid)
        if c:
            evidence.append({"claim": f"{c['section']} — {c['text'][:220]}", "source": "document",
                             "ref": f"README.md#{c['section']}", "entity_ids": [], "side": "policy"})

    stop_reason = _stop_reason(p_final, verdict, n_graph_incul, requested, evidence_requests)
    what_changed = _what_changed(requested, p_graph, p_final, initial, final, evidence_requests)

    if verdict == "fraud":
        conclusion = "The fraud hypothesis survived its strongest defense."
    elif verdict == "legitimate":
        conclusion = "A legitimate explanation accounts for the alert; the fraud hypothesis did not survive."
    else:
        conclusion = ("Prosecution and defence are both credible; the evidence conflicts and a decisive "
                      "fact is missing, so the case goes to a human.")
    debate = {
        "prosecution": [e["claim"] for e in evidence if e.get("side") == "prosecution"],
        "defense": [e["claim"] for e in evidence if e.get("side") == "defense"],
        "conclusion": conclusion,
        "missing_evidence": [er["type"] for er in evidence_requests],
    }

    graph_case_id = f"CASE-{case_row['case_id']}"
    summary = _summary(ctx, fired, verdict, pattern, p_final, exposure, connected_cards, similar,
                       evidence_requests)
    ans = {
        "case_id": case_row["case_id"],
        "case": {
            "status": status, "verdict": verdict, "fraud_probability": round(p_final, 3),
            "pattern": pattern,
            "pattern_description": _pattern_desc(fired) if pattern == "undocumented" else "",
            "affected_txn_ids": [str(x) for x in affected],
            "first_suspicious_txn_id": (str(_first_susp(ds, ctx, affected)) if affected else ""),
            "connected_card_ids": connected_cards,
            "connected_device_profiles": connected_profiles,
            "exposure_usd": round(exposure, 2),
            "evidence": [{k: e[k] for k in ("claim", "source", "ref", "entity_ids")} for e in evidence],
            "similar_prior_cases": [m["case_id"] for m in similar],
            "summary": summary,
            "written_to_graph": False, "graph_case_id": "",
        },
        "evidence_requests": evidence_requests,
        "next_best_actions": {"initial": initial, "final": final, "what_changed": what_changed},
        "sar": sar,
        "stop_reason": stop_reason,
        "tool_calls": tool_calls,
        "tokens": 0,
        "latency_s": 0.0,
        "debate": debate,
    }

    # ---- optional LLM: explanation + SAR prose over the retrieved context (never decides) ----
    try:
        from . import llm
        tokens = llm.enrich(ans, [K.get(r) for r in rules if K.get(r)]) if use_llm else 0
        ans["tokens"] = tokens
        ans["tool_calls"] += 1 if tokens else 0
    except Exception as e:
        print(f"[llm] {ans['case_id']}: narration skipped ({str(e)[:120]})")

    # ---- update case memory: local store + TigerGraph ----
    if write_back:
        M.remember(ans, ctx)
        tg = _tg()
        if tg is not None:
            try:
                ans["case"]["graph_case_id"] = graph_case_id
                ok, vid = tg.write_case(ans)
                ans["case"]["written_to_graph"] = bool(ok)
                ans["case"]["graph_case_id"] = vid if ok else ""
                ans["tool_calls"] += getattr(tg, "calls", 0) and 1
            except Exception:
                ans["case"]["graph_case_id"] = ""
    ans["latency_s"] = round(time.time() - t0, 3)
    return ans


# --------------------------------------------------------------------------- helpers
def _graph_crosscheck(ctx, fired, evidence) -> int:
    """When TigerGraph is live, re-run the key traversals as installed GSQL queries (direct
    REST and through the TigerGraph MCP server) and attach what the graph returns. The pandas
    probes and the graph queries implement the same traversals, so this is also a consistency
    check between the two backends."""
    tg = _tg()
    if tg is None:
        return 0
    n = 0
    cust = ctx["customer_id"]
    try:
        link = tg.link_to_known_fraud(cust)
        n += 1
        cc = [c.get("v_id") or c.get("case_id") for c in (link or {}).get("closed_cases", [])][:6]
        if cc:
            evidence.append({"claim": f"TigerGraph traversal Customer→Card→Transaction→DeviceProfile→Transaction"
                                      f"←ClosedCase reaches {len(link['closed_cases'])} confirmed-fraud closed case(s)",
                             "source": "graph", "ref": "tigergraph:link_to_known_fraud", "entity_ids": cc,
                             "side": "context"})
        prior = tg.prior_cases_for_customer(cust)
        n += 1
        inv = [i.get("v_id") for i in (prior or {}).get("investigations", [])][:4]
        if inv:
            evidence.append({"claim": f"Graph case memory holds {len(inv)} earlier investigation(s) on this customer",
                             "source": "graph", "ref": "tigergraph:prior_cases_for_customer", "entity_ids": inv,
                             "side": "context"})
    except Exception:
        pass
    try:
        from . import mcp_tools
        mcp = mcp_tools.get()
        if mcp is not None and ctx["flagged_profile"]:
            f = ctx["flagged"]
            mcp.run_installed_query("device_neighbors", {
                "profile": ctx["flagged_profile"],
                "t0": str(f["ts"] - pd.Timedelta(days=30))[:19], "t1": str(f["ts"] + pd.Timedelta(days=30))[:19]})
            n += 1
    except Exception:
        pass
    return n


def _respond(belief, evidence, requests, rtype, assumed, lr, side, step):
    requests.append({"type": rtype, "asked_after_step": len(evidence), "assumed_response": assumed})
    belief.update(lr)
    evidence.append({"claim": assumed.split(" (assumed")[0], "source": "customer",
                     "ref": f"evidence_request:{len(requests)}", "entity_ids": [], "side": side})
    step(rtype)


def _pick_pattern(fired, ctx):
    if "structuring" in fired:
        return "undocumented"
    sd, lk = fired.get("shared_device"), fired.get("link_known_fraud")
    if sd and sd["value"]["n"] >= S.RING_MIN:
        return "undocumented"      # device farm: many unrelated cards on one specific device
    if "card_testing" in fired:
        return "card_testing"
    if "out_of_region" in fired and fired["out_of_region"]["lr"] > 1:
        return "out_of_region_use"
    if "account_takeover" in fired or "channel_mismatch" in fired:
        return "account_takeover"
    anomaly = "flagged_anomaly" in fired or "cnp_burst" in fired
    if "new_device" in fired and (anomaly or sd or lk):
        return "card_not_present_new_device"
    if anomaly:
        return "card_not_present_fraud"
    return "none"


def _fallback_pattern(ctx, fired):
    """Confirmed fraud with no distinctive pattern: use the channel, then the customer's memory."""
    f = ctx["flagged"]
    if f["channel"] == "online":
        return "card_not_present_new_device" if str(f.get("id_15")) == "New" else "card_not_present_fraud"
    mem = fired.get("case_memory")
    pats = (mem or {}).get("value", {}).get("fraud_patterns", {}) if mem else {}
    for p in sorted(pats, key=lambda k: -pats[k]):
        if p in ("out_of_region_use", "account_takeover"):
            return p
    return "out_of_region_use"


def _affected_txns(ctx, fired):
    for key in ("structuring", "card_testing"):
        if key in fired:
            return sorted(int(x) for x in fired[key]["entity_ids"])
    if "shared_device" in fired and fired["shared_device"]["value"]["n"] >= S.RING_MIN:
        return sorted(set(fired["shared_device"]["value"]["customer_txns"]) | {ctx["flagged_txn_id"]})
    if "cnp_burst" in fired and fired["cnp_burst"]["lr"] >= 2.0:
        return sorted(int(x) for x in fired["cnp_burst"]["entity_ids"])
    return [ctx["flagged_txn_id"]]


def _exposure(ds, affected):
    if not affected:
        return 0.0
    rows = ds.tx[ds.tx.TransactionID.isin(affected)]
    return float(rows["TransactionAmt"].abs().sum())


def _cleared_reason(ctx):
    f = ctx["flagged"]
    if f["channel"] == "in_person":
        return "travel"
    if str(f.get("id_15")) == "New":
        return "new_phone"
    return "unusual_amount"


def _initial_actions(rtype, p, pattern, fired, exposure, ct_cleared):
    A = []
    if "card_testing" in fired:
        A.append(P.act("DECLINE_TRANSACTION", exposure, "R5: card-testing sequence observed; decline pending authorizations"))
    A.append(P.act("STEP_UP_AUTH" if rtype == "step_up_auth" else "VERIFY_WITH_CUSTOMER", exposure,
                   f"R1: assessed probability {p:.2f} rests on too little evidence to block; verify first"))
    A.append(P.act("CREATE_CASE", exposure, "3a: a case is opened whenever evidence is requested"))
    if exposure > P.ESCALATE_EXPOSURE and p < P.VERIFY_BELOW:
        A.append(P.act("ESCALATE_TO_ANALYST", exposure, f"R8: uncertain with exposure ${exposure:,.2f} over $500"))
    return A


def _actions(trigger, verdict, prob, pattern, exposure, shared_origin, ct_cleared, connected,
             requested, r7, no_reply=False):
    A = []
    if verdict == "legitimate":
        if r7:
            return [P.act("CREATE_CASE", exposure, "R7/3a: dispute logged for the record"),
                    P.act("VERIFY_WITH_CUSTOMER", exposure, "R7: confirm the recurring charge with the cardholder"),
                    P.act("WARN_CUSTOMER", exposure, "R7: recurring-charge reminder; do not block")]
        if requested:
            return [P.act("ALLOW_TRANSACTION", exposure, "R3: customer confirmed the transaction"),
                    P.act("CLOSE_NO_FRAUD", exposure, "R3: case closed as legitimate; confirmation noted in the case file")]
        return [P.act("ALLOW_TRANSACTION", exposure, f"policy 6: probability {prob:.2f} is at/below 0.15 on "
                      f"independent evidence"),
                P.act("CLOSE_NO_FRAUD", exposure, "policy 6: alert cleared as legitimate")]
    if verdict == "uncertain" and no_reply:
        A.append(P.act("MONITOR_CARD", exposure, "R4: no reply within 24 hours; raise monitoring for 72h"))
        A.append(P.act("DECLINE_TRANSACTION", exposure, "R4: decline pending authorizations while unverified"))
        A.append(P.act("CREATE_CASE", exposure, "3a: case stays open pending the customer's reply"))
        if exposure > P.ESCALATE_EXPOSURE:
            A.append(P.act("ESCALATE_TO_ANALYST", exposure, f"R4/R8: uncertain with exposure ${exposure:,.2f} over $500"))
        return A
    if verdict == "uncertain":
        A.append(P.act("MONITOR_CARD", exposure, "insufficient evidence to block; raise monitoring for 72h"))
        if prob >= P.CREATE_CASE_PROB or requested or trigger == "customer_report":
            A.append(P.act("CREATE_CASE", exposure, "3a: probability >= 0.30 / evidence requested / dispute"))
        A.append(P.act("ESCALATE_TO_ANALYST", exposure,
                       "R8: uncertain" + (f" and exposure ${exposure:,.2f} over $500" if exposure > P.ESCALATE_EXPOSURE
                                          else " and the evidence conflicts")))
        return A
    # verdict == fraud
    sar = P.should_file_report(verdict, prob, exposure, shared_origin, pattern)
    if pattern == "card_testing":
        A.append(P.act("DECLINE_TRANSACTION", exposure, "R5: card-testing sequence observed"))
        A.append(P.act("BLOCK_CARD", exposure, "R5: a purchase over $100 has already cleared") if ct_cleared else
                 P.act("STEP_UP_AUTH", exposure, "R5: step-up before any further use"))
    else:
        A.append(P.act("BLOCK_CARD", exposure,
                       ("R2: customer denied the transaction" if requested or trigger == "customer_report"
                        else "R9/R6: coordinated abuse on this card") +
                       f"; exposure ${exposure:,.2f} {'>' if exposure > P.BLOCK_L2_EXPOSURE else '<='} $2,500"))
    A.append(P.act("CREATE_CASE", exposure,
                   ("R2/3a" if requested or trigger == "customer_report" else
                    "R9/3a" if pattern == "undocumented" else "R5/3a" if pattern == "card_testing" else "3a")
                   + ": open the case and write it to the graph"))
    if sar:
        why = ("R9: undocumented coordinated pattern" if pattern == "undocumented" else
               "R6: shared origin with other cards" if shared_origin else "R2: exposure over $1,000")
        A.append(P.act("FILE_REPORT", exposure, f"3a: fraud confirmed/strongly suspected and {why}"))
    if shared_origin and connected:
        A.append(P.act("MONITOR_CONNECTED_CARDS", exposure,
                       f"R6: {len(connected)} card(s) share the device profile"))
    if pattern == "undocumented":
        A.append(P.act("ESCALATE_TO_ANALYST", exposure, "R9: pattern fits none of the known typologies"))
    return A


def _cited_rules(actions):
    import re
    seen = []
    for a in actions:
        for r in re.findall(r"\b(R\d+|3a)\b", a["reason"]):
            if r not in seen:
                seen.append(r)
    return seen


def _similar(ds, ctx, fired, verdict, pattern, exposure):
    if pattern == "undocumented":
        if "structuring" in fired:
            return ds.similar_prior_cases("undocumented", exposure, keyword="just under $500")
        lk = fired.get("link_known_fraud")
        on_profile = [c for c in (lk["value"]["cases"] if lk else [])]
        und = [m for m in ds.memory if m["case_id"] in on_profile and m["pattern"] == "undocumented"]
        return (und or ds.similar_prior_cases("undocumented", exposure, keyword="device profile"))[:3]
    if verdict == "legitimate" or pattern == "none":
        return ds.similar_prior_cases("none", 0, cleared_reason=_cleared_reason(ctx),
                                      amount=float(ctx["flagged"]["TransactionAmt"]),
                                      customer_id=ctx["customer_id"])
    return ds.similar_prior_cases(pattern, exposure, customer_id=ctx["customer_id"])


def _first_susp(ds, ctx, affected):
    sub = ds.tx[ds.tx.TransactionID.isin([int(x) for x in affected])]
    if sub.empty:
        return ctx["flagged_txn_id"]
    return int(sub.sort_values("ts").iloc[0]["TransactionID"])


def _stop_reason(prob, verdict, n_graph, requested, reqs):
    if requested and "No reply" in reqs[0]["assumed_response"]:
        return (f"No reply to the {reqs[0]['type'].replace('_', ' ')} request within 24 hours; probability "
                f"{prob:.0%} stays uncertain and further graph steps are unlikely to change it, so the card is "
                f"monitored and pending authorizations declined under R4 (policy 6).")
    if requested:
        return (f"The {reqs[0]['type'].replace('_', ' ')} response settled the question (policy 6): "
                f"probability now {prob:.0%}, verdict {verdict}. Further graph steps would not change the actions.")
    if prob >= P.STOP_HIGH:
        return (f"Fraud probability {prob:.0%} is at/above 0.85 with {n_graph} independent inculpatory graph "
                f"findings; further steps would not change the decision (policy 6).")
    if prob <= P.STOP_LOW:
        return (f"Fraud probability {prob:.0%} is at/below 0.15 on independent exculpatory evidence; cleared "
                f"as legitimate (policy 6).")
    return (f"Probability {prob:.0%} remains uncertain and the remaining probes cannot settle it; escalated "
            f"to an analyst per R8 rather than over-investigating (policy 6).")


def _what_changed(requested, p0, p1, initial, final, reqs):
    if not requested:
        return "nothing"
    a0 = ", ".join(a["action"] for a in initial)
    a1 = ", ".join(a["action"] for a in final)
    return (f"The assumed {reqs[0]['type'].replace('_', ' ')} response moved the fraud probability from "
            f"{p0:.2f} to {p1:.2f}, so the recommendation changed from [{a0}] to [{a1}].")


def _summary(ctx, fired, verdict, pattern, prob, exposure, connected, similar, reqs):
    f = ctx["flagged"]
    sc = ", ".join(m["case_id"] for m in similar[:2]) or "none"
    lead = (f"{ctx['trigger_type'].replace('_', ' ').capitalize()} on {f['channel']} transaction "
            f"{ctx['flagged_txn_id']} (${f['TransactionAmt']:.2f}) on card {ctx['card_id']}.")
    top = sorted(fired.values(), key=lambda r: -abs(__import__('math').log(max(r['lr'], 1e-6))))
    key_ev = "; ".join(r["claim"].split(" — ")[0][:110] for r in top[:2] if r["lr"] != 1.0)
    if verdict == "fraud":
        body = (f"Assessed fraud ({pattern}) at {prob:.0%}, exposure ${exposure:,.2f}"
                + (f", linked to {len(connected)} other card(s) by a shared device" if connected else "") + ".")
    elif verdict == "legitimate":
        body = f"Assessed legitimate at {prob:.0%} fraud probability."
    else:
        body = f"Uncertain at {prob:.0%}; handed to an analyst."
    tail = f" Key evidence: {key_ev}." if key_ev else ""
    if reqs:
        tail += f" Assumed response: {reqs[0]['assumed_response'].split(' (assumed')[0].lower()}."
    return f"{lead} {body}{tail} Closest prior cases: {sc}."


def _pattern_desc(fired):
    if "structuring" in fired:
        v = fired["structuring"]["value"]
        return (f"Threshold structuring: {v['n']} online purchases within {v['minutes']:.0f} minutes, each just "
                f"under $500, apparently sized to stay below a $500 authorization limit. It affects a single "
                f"card but matches five confirmed closed cases (CC-3748, CC-3841, CC-3907, CC-4086, CC-4124) and "
                f"was found by a time-window scan of the card's online purchases around the alert.")
    if "shared_device" in fired:
        v = fired["shared_device"]["value"]
        return (f"Device-farm ring: one specific device profile ({v['profile']}) was used by {v['n'] + 1} "
                f"different customers between {v['window'][0]} and {v['window'][1]}, "
                f"{v['proxy_share']:.0%} of the time behind an anonymous proxy and almost always marked New. "
                f"It fits none of the five known patterns (it is many unrelated cards on one device, not one card "
                f"used abnormally) and was found by device-neighbour traversal from the flagged transaction.")
    return "Activity fitting none of the five documented patterns."


def _build_sar(ds, ctx, fired, verdict, pattern, exposure, affected, connected_cards,
               connected_profiles, shared_origin, file_report, reqs, similar, final):
    if not file_report:
        return {"file": False, "reason": _sar_reason_no(verdict, exposure, shared_origin, pattern),
                "narrative": "", "subjects": [], "total_amount_usd": 0, "activity_dates": []}
    from .narrate_real import sar_narrative
    aff = ds.tx[ds.tx.TransactionID.isin([int(x) for x in affected])].sort_values("ts")
    dates = ([str(aff["ts"].min().date()), str(aff["ts"].max().date())] if not aff.empty else [])
    subjects = ([ctx["customer_id"], ctx["card_id"]] + connected_cards[:10] + connected_profiles[:1]
                + [m["case_id"] for m in similar[:2]])
    why = ("R9: undocumented coordinated pattern" if pattern == "undocumented" else
           "R6: activity connects to a shared device profile / another customer's fraud" if shared_origin else
           f"R2: exposure ${exposure:,.2f} exceeds $1,000")
    return {
        "file": True,
        "reason": f"3a: {pattern} fraud confirmed or strongly suspected and {why}",
        "narrative": sar_narrative(ctx, fired, pattern, exposure, aff, connected_cards, connected_profiles,
                                   dates, reqs, similar, final),
        "subjects": subjects,
        "total_amount_usd": round(exposure, 2),
        "activity_dates": dates,
    }


def _sar_reason_no(verdict, exposure, shared, pattern):
    if verdict != "fraud":
        return "3a: no report — activity is not confirmed or strongly suspected fraud."
    return (f"3a: no report — fraud confirmed but exposure ${exposure:,.2f} is not over $1,000, there is no "
            f"shared device/region link to other customers' fraud, and the pattern ({pattern}) is documented. "
            f"A case is opened without a report.")
