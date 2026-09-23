"""
Run CLARA on all 20 case-pack cases and write one answer file per case.

  python run_cases.py                 # -> cases/HHG-XXX.json  (submission format)

Prereq: python data/build_cache.py  (builds dataset/_slim.parquet once).
"""
import json
import os

from dotenv import load_dotenv

from clara.investigator import investigate
from clara.realdata import load

load_dotenv()


def main():
    ds = load()
    os.makedirs("cases", exist_ok=True)
    rows = []
    for _, case in ds.case_pack.iterrows():
        ans = investigate(ds, case)
        with open(os.path.join("cases", f"{ans['case_id']}.json"), "w") as f:
            json.dump(ans, f, indent=2, default=str)
        c = ans["case"]
        rows.append({
            "case": ans["case_id"], "trigger": case["trigger_type"],
            "verdict": c["verdict"], "prob": c["fraud_probability"], "pattern": c["pattern"],
            "exposure": c["exposure_usd"],
            "initial": "|".join(a["action"] for a in ans["next_best_actions"]["initial"]),
            "final": "|".join(a["action"] for a in ans["next_best_actions"]["final"]),
            "sar": ans["sar"]["file"], "similar": ",".join(c["similar_prior_cases"][:2]),
        })
    import pandas as pd
    df = pd.DataFrame(rows)
    pd.set_option("display.max_colwidth", 40); pd.set_option("display.width", 200)
    print(df.to_string(index=False))
    print(f"\nWrote {len(rows)} answer files to cases/")
    print("verdicts:", df.verdict.value_counts().to_dict())
    print("SARs filed:", int(df.sar.sum()))


if __name__ == "__main__":
    main()
