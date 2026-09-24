"""
Check every cases/HHG-XXX.json against the README answer format and the Fraud Policy.

  python validate_answers.py
"""
import glob
import json
import os
import sys

import pandas as pd

from clara import policy_real as P

ACTIONS = {"ALLOW_TRANSACTION", "DECLINE_TRANSACTION", "MONITOR_CARD", "MONITOR_CONNECTED_CARDS",
           "WARN_CUSTOMER", "VERIFY_WITH_CUSTOMER", "STEP_UP_AUTH", "BLOCK_CARD", "BLOCK_ALL_CARDS",
           "GENERATE_REPORT", "CREATE_CASE", "FILE_REPORT", "ESCALATE_TO_ANALYST", "CLOSE_NO_FRAUD"}
PATTERNS = {"card_testing", "card_not_present_fraud", "card_not_present_new_device", "out_of_region_use",
            "account_takeover", "undocumented", "none"}
TOP = ["case_id", "case", "evidence_requests", "next_best_actions", "sar", "stop_reason", "tool_calls",
       "tokens", "latency_s"]
CASE = ["status", "verdict", "fraud_probability", "pattern", "pattern_description", "affected_txn_ids",
        "first_suspicious_txn_id", "connected_card_ids", "connected_device_profiles", "exposure_usd",
        "evidence", "similar_prior_cases", "summary", "written_to_graph", "graph_case_id"]


def main():
    tx = pd.read_parquet("dataset/_slim.parquet", columns=["TransactionID", "customer_id", "TransactionAmt"])
    tids = set(tx.TransactionID.astype(str))
    amt = dict(zip(tx.TransactionID.astype(str), tx.TransactionAmt))
    closed = pd.read_csv("dataset/closed_cases_history.csv")
    ccids = set(closed.case_id)
    cards = set(closed.card_id) | set(pd.read_csv("dataset/case_pack.csv").card_id) | {
        c for s in closed.connected_card_ids.dropna() for c in str(s).split("|")}
    custs = set(tx.customer_id)
    problems = 0
    files = sorted(glob.glob("cases/HHG-*.json"))
    # the graders read cases/ directly: exactly one <case_id>.json per case_pack row, nothing else
    expected = {f"{c}.json" for c in pd.read_csv("dataset/case_pack.csv").case_id}
    present = {os.path.basename(f) for f in glob.glob("cases/*")}
    for x in sorted(expected - present):
        print(f"ERR missing cases/{x}"); problems += 1
    for x in sorted(present - expected):
        print(f"ERR unexpected file cases/{x}"); problems += 1
    for fn in files:
        if json.load(open(fn))["case_id"] != os.path.basename(fn)[:-5]:
            print(f"ERR {fn}: case_id does not match file name"); problems += 1
    for fn in files:
        a = json.load(open(fn))
        c, sar, nba = a["case"], a["sar"], a["next_best_actions"]
        errs = [f"missing top-level {k}" for k in TOP if k not in a]
        errs += [f"missing case.{k}" for k in CASE if k not in c]
        if c["pattern"] not in PATTERNS:
            errs.append(f"bad pattern {c['pattern']}")
        if c["pattern"] == "undocumented" and not c["pattern_description"]:
            errs.append("undocumented without pattern_description")
        errs += [f"unknown txn {t}" for t in c["affected_txn_ids"] if t not in tids]
        errs += [f"unknown closed case {x}" for x in c["similar_prior_cases"] if x not in ccids]
        exp = round(sum(abs(amt[t]) for t in c["affected_txn_ids"] if t in amt), 2)
        if abs(exp - c["exposure_usd"]) > 0.02:
            errs.append(f"exposure {c['exposure_usd']} != sum(affected) {exp}")
        if c["verdict"] == "legitimate" and (c["affected_txn_ids"] or c["exposure_usd"] or sar["file"]):
            errs.append("legitimate verdict must have no affected txns / exposure / SAR")
        for stage in ("initial", "final"):
            for x in nba[stage]:
                if x["action"] not in ACTIONS:
                    errs.append(f"bad action {x['action']}")
                elif x["route"] != P.route_for(x["action"], c["exposure_usd"]):
                    errs.append(f"{stage} {x['action']} route {x['route']} != {P.route_for(x['action'], c['exposure_usd'])}")
        if not a["evidence_requests"] and nba["initial"] != nba["final"]:
            errs.append("no evidence requested but initial != final")
        if sar["file"] != any(x["action"] == "FILE_REPORT" for x in nba["final"]):
            errs.append("sar.file disagrees with FILE_REPORT in final actions")
        if sar["file"]:
            n = sar["narrative"].count(". ") + 1
            if not (6 <= n <= 14):
                errs.append(f"SAR narrative has ~{n} sentences")
            if len(sar["activity_dates"]) != 2:
                errs.append("SAR activity_dates must be two dates")
            bad = [s for s in sar["subjects"] if not (s in custs or s in cards or s in ccids or " | " in s
                                                      or s.endswith(("-K1", "-K2", "-K3")))]
            errs += [f"unknown SAR subject {s}" for s in bad]
        else:
            if sar["narrative"] or sar["subjects"] or sar["total_amount_usd"] or sar["activity_dates"]:
                errs.append("sar.file false but narrative/subjects/amount/dates not empty")
        if any(x["action"] == "BLOCK_ALL_CARDS" for x in nba["final"]):
            errs.append("BLOCK_ALL_CARDS recommended (R10: needs two cards with confirmed fraud)")
        status = "OK " if not errs else "ERR"
        problems += len(errs)
        print(f"{status} {a['case_id']}  {c['verdict']:<10} {c['pattern']:<28} graph={c['written_to_graph']}"
              + ("".join(f"\n      - {e}" for e in errs)))
    print(f"\n{len(files)} files, {problems} problem(s)")
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
