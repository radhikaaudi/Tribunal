"""
Loader + indexes for the real HHGOA dataset (via the slim parquet cache).

Investigation is at the CUSTOMER level (customer_id links transactions; card1 is 1:1
with customer). The card_id string (e.g. C12382-K1) is used as a label only.
"""
from __future__ import annotations

import functools
import re
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
        # browser-engine strings (Trident/7.0, rv:11.0) are software fingerprints, not hardware ids
        if device_info in GENERIC_DEVICEINFO or device_info.startswith(("Trident/", "rv:")):
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
                "opened_at": pd.to_datetime(r.opened_at),
            })
        self._mem_by_pattern = {}
        for m in self.memory:
            self._mem_by_pattern.setdefault(m["pattern"], []).append(m)

        # txn -> confirmed-fraud closed case (graph edge ClosedCase-INVOLVES->Transaction)
        self.fraud_case_by_txn = {}
        for r in fraud.itertuples():
            for t in str(r.txn_ids).split("|"):
                t = t.strip()
                if t.isdigit():
                    self.fraud_case_by_txn[int(t)] = r.case_id
        # cleared cases carry the reason the alert was a false alarm + the alerted amount
        for m in self.memory:
            m["cleared_reason"] = ""
            m["alert_amount"] = None
            if m["outcome"] == "cleared":
                n = m["notes"]
                m["cleared_reason"] = ("travel" if "travel" in n else "new_phone" if "new phone" in n
                                       else "unusual_amount" if "Amount unusual" in n else "other")
                mt = re.search(r"\$([\d,]+\.\d+)", n)
                m["alert_amount"] = float(mt.group(1).replace(",", "")) if mt else None
        self._mem_by_customer = {}
        for m in self.memory:
            self._mem_by_customer.setdefault(m["customer_id"], []).append(m)

    def device_linked_to_fraud(self, profile: str) -> bool:
        return bool(profile) and profile in self.fraud_device_profiles

    def closed_cases_on_profile(self, profile: str) -> list[str]:
        """Confirmed-fraud closed cases whose transactions used this device profile."""
        if not profile:
            return []
        tids = self.same_device(profile)["TransactionID"]
        return sorted({self.fraud_case_by_txn[t] for t in tids if t in self.fraud_case_by_txn})

    def customer_cases(self, customer_id: str, before=None) -> list[dict]:
        """Prior closed cases on this customer's cards (the customer's own case memory)."""
        mem = self._mem_by_customer.get(customer_id, [])
        if before is not None:
            mem = [m for m in mem if m["opened_at"] < before]
        return mem

    def similar_prior_cases(self, pattern: str, exposure: float, k: int = 3, *,
                            customer_id: str | None = None, cleared_reason: str | None = None,
                            amount: float | None = None, keyword: str | None = None) -> list[dict]:
        """Hybrid case-memory retrieval over the closed cases.

        fraud patterns -> same pattern, nearest exposure, same-customer cases first;
        legitimate    -> cleared cases with the same false-alarm reason, nearest alerted amount;
        undocumented  -> cases whose analyst notes describe the same behaviour (keyword).
        """
        if pattern == "none":
            pool = [m for m in self.memory if m["outcome"] == "cleared"
                    and (cleared_reason is None or m["cleared_reason"] == cleared_reason)]
            key = (lambda m: abs((m["alert_amount"] or 0) - (amount or 0)))
        else:
            pool = [m for m in self._mem_by_pattern.get(pattern, [])
                    if keyword is None or keyword.lower() in m["notes"].lower()]
            if not pool:
                pool = [m for m in self.memory if m["outcome"] == "confirmed_fraud"]
            key = (lambda m: abs(m["exposure_usd"] - exposure))
        own = [m for m in pool if customer_id and m["customer_id"] == customer_id]
        rest = [m for m in pool if m not in own]
        return (sorted(own, key=key)[:1] + sorted(rest, key=key))[:k]


@functools.lru_cache(maxsize=1)
def load() -> Dataset:
    return Dataset()
