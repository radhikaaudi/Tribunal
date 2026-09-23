"""
Investigate a single case from the command line.

  python run_case.py --card1 50005 --trigger "risk_score>0.5" --id DEMO-01
  python run_case.py --bench B-01          # use a row from data/benchmark.csv
"""
import argparse
import json
import os

from dotenv import load_dotenv

from clara.agent import investigate
from clara.graph_client import make_client
from clara.narrate import fmt_value

load_dotenv()


def _print_step(ev, prob):
    arrow = "up" if ev.delta >= 0 else "down"
    print(f"  [{ev.key:16s}] {ev.label}: {fmt_value(ev.raw_value)}  "
          f"({ev.prob_before:.0%} -> {ev.prob_after:.0%} {arrow})")
    print(f"      why: {ev.rationale}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--card1", type=int)
    ap.add_argument("--trigger", default="risk_score_alert")
    ap.add_argument("--id", default="DEMO-01")
    ap.add_argument("--bench", help="benchmark id from data/benchmark.csv")
    args = ap.parse_args()

    client = make_client()

    card1, trigger, case_id = args.card1, args.trigger, args.id
    if args.bench:
        import pandas as pd
        bench = pd.read_csv(os.path.join("data", "benchmark.csv"))
        row = bench[bench.bench_id == args.bench].iloc[0]
        card1, trigger, case_id = int(row.card1), row.reason, args.bench

    print(f"\n=== CLARA investigation {case_id} (card1={card1}, trigger='{trigger}') ===")
    case = investigate(client, case_id, card1, trigger, on_step=_print_step)

    print(f"\n  NBA (initial): {case.nba_initial.name} [{case.nba_initial.approval_route}]")
    print(f"  STOP: {case.stop_reason}")
    print(f"  Final confidence: {case.final_prob:.0%}  |  typology: {case.typology}")
    print(f"  NBA (final):   {case.nba_final.name} [{case.nba_final.approval_route}]"
          f" - {case.nba_final.reason}")
    if case.sar:
        print(f"  SAR filed: {case.sar['what']} (conf {case.sar['confidence']:.0%})")
    print(f"\n  Narrative:\n    {case.narrative}\n")

    out = os.path.join("cases", f"{case_id}.answer.json")
    os.makedirs("cases", exist_ok=True)
    with open(out, "w") as f:
        json.dump(case.to_dict(), f, indent=2)
    print(f"  answer file -> {out}")


if __name__ == "__main__":
    main()
