"""
One-time slim cache builder for the real HHGOA dataset.

Reads the big dataset/transactions.csv (675MB) + identity.csv once and writes a slim
parquet (dataset/_slim.parquet) with only the columns CLARA needs, plus a derived
device_profile. Everything downstream loads the parquet in ~1-2s instead of re-parsing.

  python data/build_cache.py
"""
import os
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
DS = os.path.join(os.path.dirname(HERE), "dataset")

TX_COLS = ["TransactionID", "customer_id", "ts", "TransactionAmt", "ProductCD",
           "channel", "risk_score", "addr1", "addr2", "card4", "card6",
           "P_emaildomain", "R_emaildomain"] + [f"M{i}" for i in range(1, 10)]
ID_COLS = ["TransactionID", "DeviceInfo", "DeviceType", "id_15", "id_23",
           "id_30", "id_31", "id_33"]


def main():
    print("reading transactions (subset of 397 cols)...")
    tx = pd.read_csv(os.path.join(DS, "transactions.csv"), usecols=TX_COLS)
    print(f"  {len(tx):,} rows")
    tx["ts"] = pd.to_datetime(tx["ts"], errors="coerce")

    print("reading identity...")
    idf = pd.read_csv(os.path.join(DS, "identity.csv"), usecols=ID_COLS)
    print(f"  {len(idf):,} rows")

    m = tx.merge(idf, on="TransactionID", how="left")

    # device profile = DeviceInfo | OS | browser | screen  (answer-format style)
    def prof(r):
        if pd.isna(r.DeviceInfo) and pd.isna(r.id_30):
            return ""
        parts = [str(r.DeviceInfo) if pd.notna(r.DeviceInfo) else "?",
                 str(r.id_30) if pd.notna(r.id_30) else "?",
                 str(r.id_31) if pd.notna(r.id_31) else "?",
                 str(r.id_33) if pd.notna(r.id_33) else "?"]
        return " | ".join(parts)

    m["device_profile"] = m.apply(prof, axis=1)
    m["f_flags"] = (m[[f"M{i}" for i in range(1, 10)]] == "F").sum(axis=1)

    out = os.path.join(DS, "_slim.parquet")
    m.to_parquet(out, index=False)
    print(f"wrote {out}  ({len(m):,} rows, {len(m.columns)} cols)")
    print("device profiles (non-empty):", (m.device_profile != "").sum())


if __name__ == "__main__":
    main()
