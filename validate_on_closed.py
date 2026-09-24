"""
Validation: does the Defender wrongly clear real fraud?

Runs the Tribunal on a random sample of the 5,565 CLOSED cases (where the true outcome
is known) and reports the confusion between our verdict and the truth. The number that
answers the worry is the FALSE-NEGATIVE rate: known frauds we call 'legitimate'.
"""
import sys
import pandas as pd

from tribunal.realdata import load
from tribunal.investigator import investigate

N_FRAUD = int(sys.argv[1]) if len(sys.argv) > 1 else 120
N_CLEAR = int(sys.argv[2]) if len(sys.argv) > 2 else 80


def first_txn(row):
    if pd.notna(row.first_fraud_txn_id):
        return int(float(row.first_fraud_txn_id))
    ids = str(row.txn_ids).split("|")
    return int(float(ids[0])) if ids and ids[0].strip() else None


def run_one(ds, row):
    tid = first_txn(row)
    if tid is None:
        return None
    f = ds.txn(tid)
    if f is None:
        return None
    case_row = {"case_id": row.case_id, "customer_id": row.customer_id, "card_id": row.card_id,
                "flagged_txn_id": tid, "trigger_type": "risk_score",
                "risk_score": float(f["risk_score"])}
    return investigate(ds, case_row)["case"]["verdict"]


def main():
    ds = load()
    fraud = ds.closed[ds.closed.outcome == "confirmed_fraud"].sample(N_FRAUD, random_state=42)
    clear = ds.closed[ds.closed.outcome == "cleared"].sample(N_CLEAR, random_state=42)

    tally = {"fraud": {"fraud": 0, "uncertain": 0, "legitimate": 0, "skipped": 0},
             "cleared": {"fraud": 0, "uncertain": 0, "legitimate": 0, "skipped": 0}}

    for truth, sample in [("fraud", fraud), ("cleared", clear)]:
        for row in sample.itertuples():
            v = run_one(ds, row)
            tally[truth]["skipped" if v is None else v] += 1

    print("\n=== Tribunal vs known truth (closed cases) ===\n")
    print(f"{'':12}{'->fraud':>9}{'->uncertain':>12}{'->legit':>9}{'skipped':>9}")
    for truth in ("fraud", "cleared"):
        t = tally[truth]
        print(f"TRUE {truth:8}{t['fraud']:>9}{t['uncertain']:>12}{t['legitimate']:>9}{t['skipped']:>9}")

    f = tally["fraud"]; c = tally["cleared"]
    nf = f["fraud"] + f["uncertain"] + f["legitimate"]
    nc = c["fraud"] + c["uncertain"] + c["legitimate"]
    print("\n--- the numbers that matter ---")
    if nf:
        print(f"FALSE NEGATIVE (real fraud we cleared as 'legitimate'): "
              f"{f['legitimate']}/{nf} = {f['legitimate']/nf:.0%}   <- the Defender's danger")
        print(f"Caught outright (fraud) or flagged for review (uncertain): "
              f"{(f['fraud']+f['uncertain'])}/{nf} = {(f['fraud']+f['uncertain'])/nf:.0%}")
    if nc:
        print(f"Correctly cleared legit cases (no over-block): "
              f"{c['legitimate']}/{nc} = {c['legitimate']/nc:.0%}")
        print(f"Over-blocked a legit customer as 'fraud': {c['fraud']}/{nc} = {c['fraud']/nc:.0%}")


if __name__ == "__main__":
    main()
