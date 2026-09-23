"""
Loader + indexes for the real HHGOA dataset (via the slim parquet cache).

Investigation is at the CUSTOMER level (customer_id links transactions; card1 is 1:1
with customer). The card_id string (e.g. C12382-K1) is used as a label only.
"""
from __future__ import annotations

import functools
import os

import pandas as pd

DS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dataset")
DAY = pd.Timedelta(days=1)
# Generic OS-only DeviceInfo strings are software fingerprints shared by hundreds of
# unrelated users - never a link. A specific hardware/build string (e.g. "SM-G935F
# Build/NRD90M") IS an identifier; many cards on one such profile is a device farm.
GENERIC_DEVICEINFO = {"Windows", "MacOS", "iOS Device", "Linux", "?", "nan", ""}
SPECIFIC_CAP = 150  # sanity bound; the 30-day window in probe_shared_device bounds the actual ring


class Dataset:
    def __init__(self, ds_dir: str = DS):
        slim = os.path.join(ds_dir, "_slim.parquet")
        if not os.path.exists(slim):
            raise FileNotFoundError(f"{slim} missing - run: python data/build_cache.py")
        self.tx = pd.read_parquet(slim)
        if not pd.api.types.is_datetime64_any_dtype(self.tx["ts"]):
            self.tx["ts"] = pd.to_datetime(self.tx["ts"], errors="coerce")
        self.closed = pd.read_csv(os.path.join(ds_dir, "closed_cases_history.csv"))
        self.case_pack = pd.read_csv(os.path.join(ds_dir, "case_pack.csv"))
        self._by_cust = {c: g.sort_values("ts") for c, g in self.tx.groupby("customer_id")}
        # distinct customers per device profile -> identify hub (generic) vs specific profiles
        nz = self.tx[self.tx.device_profile != ""]
        self._profile_ncust = nz.groupby("device_profile")["customer_id"].nunique().to_dict()
        self._build_fraud_memory()

    def is_specific_profile(self, profile: str) -> bool:
        """True if the profile carries a specific hardware id (can link accounts), not a generic OS."""
        if not profile:
            return False
        device_info = profile.split(" | ")[0].strip()
        if device_info in GENERIC_DEVICEINFO:
            return False
        return self._profile_ncust.get(profile, 0) <= SPECIFIC_CAP

    # ---- per-customer history -------------------------------------------------
    def customer_txns(self, customer_id: str) -> pd.DataFrame:
        return self._by_cust.get(customer_id, self.tx.iloc[0:0])

    def txn(self, txn_id: int):
        r = self.tx[self.tx.TransactionID == int(txn_id)]
        return r.iloc[0] if len(r) else None

    def same_device(self, profile: str) -> pd.DataFrame:
        if not profile:
            return self.tx.iloc[0:0]
        return self.tx[self.tx.device_profile == profile]

    def same_region(self, addr1) -> pd.DataFrame:
        if pd.isna(addr1):
            return self.tx.iloc[0:0]
        return self.tx[self.tx.addr1 == addr1]

    # ---- closed-case memory + known-fraud fingerprints -----------------------
    def _build_fraud_memory(self):
        fraud = self.closed[self.closed.outcome == "confirmed_fraud"]
        fraud_txns = set()
        for s in fraud["txn_ids"].dropna():
            for t in str(s).split("|"):
                t = t.strip()
                if t.isdigit():
                    fraud_txns.add(int(t))
        self.fraud_txn_ids = fraud_txns
        self.fraud_customers = set(fraud["customer_id"].dropna())
        ftx = self.tx[self.tx.TransactionID.isin(fraud_txns)]
        # only SPECIFIC fraud device profiles taint - a generic fingerprint on a fraud txn is meaningless
        self.fraud_device_profiles = set(p for p in ftx.device_profile.unique()
                                         if self.is_specific_profile(p))
        # closed cases as memory records
        self.memory = []
        for r in self.closed.itertuples():
            self.memory.append({
                "case_id": r.case_id, "customer_id": r.customer_id, "card_id": r.card_id,
                "pattern": r.pattern, "outcome": r.outcome,
                "exposure_usd": float(r.exposure_usd) if pd.notna(r.exposure_usd) else 0.0,
                "n_txns": int(r.n_txns) if pd.notna(r.n_txns) else 0,
                "report_filed": r.report_filed,
                "connected_card_ids": [c for c in str(r.connected_card_ids).split("|")
                                       if pd.notna(r.connected_card_ids) and c and c != "nan"],
                "notes": r.analyst_notes if pd.notna(r.analyst_notes) else "",
            })
        self._mem_by_pattern = {}
        for m in self.memory:
            self._mem_by_pattern.setdefault(m["pattern"], []).append(m)

    def device_linked_to_fraud(self, profile: str) -> bool:
        return bool(profile) and profile in self.fraud_device_profiles

    def similar_prior_cases(self, pattern: str, exposure: float, k: int = 3) -> list[dict]:
        """Retrieve closed cases of the same pattern, ranked by exposure closeness."""
        pool = self._mem_by_pattern.get(pattern, [])
        if not pool:
            # fall back to any confirmed-fraud memory
            pool = [m for m in self.memory if m["outcome"] == "confirmed_fraud"]
        ranked = sorted(pool, key=lambda m: abs(m["exposure_usd"] - exposure))
        return ranked[:k]


@functools.lru_cache(maxsize=1)
def load() -> Dataset:
    return Dataset()
