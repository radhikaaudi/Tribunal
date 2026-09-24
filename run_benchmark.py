"""
Run every benchmark trigger and write one answer file per case.
  python run_benchmark.py
"""
import json
import os

from dotenv import load_dotenv
import pandas as pd

from tribunal.agent import investigate
from tribunal.graph_client import make_client

load_dotenv()


def main():
    client = make_client()
    bench = pd.read_csv(os.path.join("data", "benchmark.csv"))
    os.makedirs("cases", exist_ok=True)
    summary = []
    for row in bench.itertuples():
        case = investigate(client, row.bench_id, int(row.card1), row.reason)
        with open(os.path.join("cases", f"{row.bench_id}.answer.json"), "w") as f:
            json.dump(case.to_dict(), f, indent=2)
        summary.append({
            "bench_id": row.bench_id, "card1": int(row.card1), "hint": row.hint,
            "prior": round(case.prior_prob, 2), "final": round(case.final_prob, 2),
            "typology": case.typology,
            "nba_initial": case.nba_initial.name,
            "nba_final": case.nba_final.name,
            "approval": case.nba_final.approval_route,
            "steps": len(case.evidence),
            "sar": bool(case.sar),
        })
    df = pd.DataFrame(summary)
    print(df.to_string(index=False))
    df.to_csv(os.path.join("cases", "_summary.csv"), index=False)
    print(f"\nWrote {len(summary)} answer files to cases/")


if __name__ == "__main__":
    main()
