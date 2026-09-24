"""
Evidence probes over the real dataset. Each returns a structured evidence dict
(claim/source/ref/entity_ids) plus a likelihood ratio and the pattern it supports,
or None if it doesn't fire. The investigator turns these into belief updates + the
case's evidence list.
"""
from __future__ import annotations

import pandas as pd

HOUR = pd.Timedelta(hours=1)


def _ev(key, claim, ref, entity_ids, lr, pattern=None, source="graph", value=None):
    return {"key": key, "claim": claim, "source": source, "ref": ref,
            "entity_ids": [str(e) for e in entity_ids], "lr": lr,
            "pattern": pattern, "value": value}


def history_summary(ds, cust):
    h = ds.customer_txns(cust)
    if h.empty:
        return {"n": 0, "regions": set(), "amt_p95": 0, "products": set(), "channels": set()}
    return {
        "n": len(h),
        "regions": set(h["addr1"].dropna().tolist()),
        "amt_p95": float(h["TransactionAmt"].quantile(0.95)),
        "amt_med": float(h["TransactionAmt"].median()),
        "products": set(h["ProductCD"].dropna().tolist()),
        "channels": set(h["channel"].dropna().tolist()),
        "emails": set(h["P_emaildomain"].dropna().tolist()),
    }


def probe_flagged_anomaly(ds, ctx):
    f, hist = ctx["flagged"], ctx["histsum"]
    if hist["n"] <= 1:
        return None
    unusual = []
    if f["TransactionAmt"] > max(hist["amt_p95"], 1) * 1.2:
        unusual.append(f"amount ${f['TransactionAmt']:.2f} exceeds the card's 95th-pct ${hist['amt_p95']:.2f}")
    if pd.notna(f["ProductCD"]) and f["ProductCD"] not in hist["products"]:
        unusual.append(f"product code {f['ProductCD']} never used by this card before")
    if not unusual:
        return _ev("flagged_anomaly", "Flagged transaction is consistent with the card's history",
                   f"query:history({ctx['customer_id']})", [ctx["flagged_txn_id"]], 0.7)
    return _ev("flagged_anomaly", "Flagged transaction is unusual: " + "; ".join(unusual),
               f"query:history({ctx['customer_id']})", [ctx["flagged_txn_id"]], 2.0,
               pattern="card_not_present_fraud")


def probe_card_testing(ds, ctx):
    h = ctx["hist"]
    on = h[h["channel"] == "online"].sort_values("ts")
    if len(on) < 3:
        return None
    small = on[on["TransactionAmt"] < 5.0]
    for i in range(len(small)):
        w = small[(small["ts"] >= small.iloc[i]["ts"]) & (small["ts"] <= small.iloc[i]["ts"] + HOUR)]
        if len(w) >= 3:
            after = on[(on["ts"] > w.iloc[-1]["ts"]) & (on["ts"] <= w.iloc[-1]["ts"] + pd.Timedelta(hours=6))]
            big = after[after["TransactionAmt"] > 50]
            ids = w["TransactionID"].tolist() + big["TransactionID"].tolist()
            cleared = len(big) > 0 and big["TransactionAmt"].max() > 100
            claim = (f"{len(w)} online authorizations under $5 within an hour"
                     + (f", then a ${big['TransactionAmt'].max():.2f} purchase" if len(big) else ""))
            return _ev("card_testing", claim, f"query:card_window({ctx['card_id']},1h)",
                       ids, 9.0 if cleared else 5.0, pattern="card_testing",
                       value={"cleared_over_100": cleared})
    return None


def probe_cnp_burst(ds, ctx):
    f, h = ctx["flagged"], ctx["hist"]
    if f["channel"] != "online":
        return None
    w = h[(h["ts"] >= f["ts"] - pd.Timedelta(hours=48)) & (h["ts"] <= f["ts"] + pd.Timedelta(hours=48))]
    online = w[w["channel"] == "online"]
    if 2 <= len(online) <= 6:
        return _ev("cnp_burst", f"{len(online)} online purchases within a 48h window around the flagged one",
                   f"query:window({ctx['card_id']},48h)", online["TransactionID"].tolist(),
                   1.6, pattern="card_not_present_fraud")
    return None


def probe_new_device(ds, ctx):
    f = ctx["flagged"]
    if f.get("channel") != "online":
        return None
    new = str(f.get("id_15")) == "New"
    proxy = pd.notna(f.get("id_23")) and "PROXY" in str(f.get("id_23"))
    if not new and not proxy:
        return None
    bits = []
    if new:
        bits.append("device marked New for this account")
    if proxy:
        bits.append(f"behind a proxy ({f.get('id_23')})")
    # 'New' alone is weak (~43% of online txns; people buy new phones). Proxy is rarer/stronger.
    lr = 2.6 if (new and proxy) else 2.2 if proxy else 1.3
    return _ev("new_device", "Flagged transaction from a " + " and ".join(bits),
               f"query:identity({ctx['flagged_txn_id']})", [ctx["flagged_txn_id"]],
               lr, pattern="card_not_present_new_device", value={"new": new, "proxy": proxy})


def probe_out_of_region(ds, ctx):
    f = ctx["flagged"]
    if f["channel"] != "in_person" or pd.isna(f["addr1"]):
        return None
    h = ctx["hist"]
    prior = h[h["ts"] < f["ts"]]
    home = set(prior["addr1"].dropna())
    if not home or f["addr1"] in home:
        return None  # familiar region -> local purchase, not out-of-region
    # trip vs clone: several days of activity in the new region = a trip (legit);
    # an isolated purchase WHILE home-region activity continues = a clone (fraud)
    in_region = h[h["addr1"] == f["addr1"]]
    region_days = in_region["ts"].dt.date.nunique()
    near = h[(h["ts"] >= f["ts"] - pd.Timedelta(days=2)) & (h["ts"] <= f["ts"] + pd.Timedelta(days=2))
             & (h["addr1"].isin(home))]
    concurrent_home = len(near) > 0
    if region_days >= 3 and not concurrent_home:
        return _ev("out_of_region", f"Several days of activity in region {f['addr1']} with no "
                   f"concurrent home activity — consistent with travel, not a clone",
                   f"query:regions({ctx['customer_id']})", [ctx["flagged_txn_id"]], 0.6)
    claim = f"Card-present use in region {f['addr1']} where the cardholder has no history"
    if concurrent_home:
        claim += " while home-region activity continues — the card cannot be in two places at once"
    return _ev("out_of_region", claim, f"query:regions({ctx['customer_id']})",
               [ctx["flagged_txn_id"]], 3.2 if concurrent_home else 2.0, pattern="out_of_region_use")


def probe_account_takeover(ds, ctx):
    f, hist = ctx["flagged"], ctx["histsum"]
    if len(hist["channels"]) < 2:
        return None
    if int(f.get("f_flags") or 0) >= 3 and str(f.get("id_15")) == "New":
        return _ev("account_takeover", "Mixed-channel activity with match-flag anomalies and a new "
                   "device, consistent with stolen credentials rather than a stolen number",
                   f"query:identity({ctx['flagged_txn_id']})", [ctx["flagged_txn_id"]], 3.0,
                   pattern="account_takeover")
    return None


def probe_shared_device(ds, ctx):
    prof = ctx["flagged_profile"]
    # only distinctive (non-hub) profiles link accounts; a common fingerprint does not
    if not ds.is_specific_profile(prof):
        return None
    f = ctx["flagged"]
    same = ds.same_device(prof)
    # shared within a +/-30 day window of the flagged transaction
    win = same[(same["ts"] >= f["ts"] - pd.Timedelta(days=30))
               & (same["ts"] <= f["ts"] + pd.Timedelta(days=30))]
    others = sorted(set(win["customer_id"]) - {ctx["customer_id"]})
    if not others:
        return None
    cards = [ds_cardid(ds, c) for c in others]
    strength = 5.0 if len(others) >= 3 else 2.5
    return _ev("shared_device", f"Distinctive device profile shared with {len(others)} other card(s) "
               f"within 30 days ({', '.join(cards[:6])}{'...' if len(cards) > 6 else ''})",
               f"query:device_neighbors('{prof[:40]}...')", others, strength,
               value={"connected_cards": cards, "profile": prof, "n": len(others)})


def probe_link_known_fraud(ds, ctx):
    prof = ctx["flagged_profile"]
    reasons, ids = [], []
    if not ds.is_specific_profile(prof):
        return None
    if ds.device_linked_to_fraud(prof):
        reasons.append("device profile appears on a confirmed-fraud closed case")
    # any shared-device peer that is a known fraud customer
    if prof:
        peers = set(ds.same_device(prof)["customer_id"]) - {ctx["customer_id"]}
        fraud_peers = peers & ds.fraud_customers
        if fraud_peers:
            reasons.append(f"shares a device with known-fraud customer(s) {sorted(fraud_peers)[:3]}")
            ids += sorted(fraud_peers)
    if not reasons:
        return None
    return _ev("link_known_fraud", "; ".join(reasons).capitalize(),
               "query:link_to_known_fraud", ids or [ctx["flagged_txn_id"]], 5.0)


def probe_recurring_match(ds, ctx):
    """R7: disputed charge matches the cardholder's own recurring pattern."""
    f, h = ctx["flagged"], ctx["hist"]
    prior = h[h["ts"] < f["ts"]]
    match = prior[(prior["TransactionAmt"].sub(f["TransactionAmt"]).abs() < 1.0)
                  & (prior["P_emaildomain"] == f["P_emaildomain"])]
    if len(match) >= 2:
        return _ev("recurring_match", f"Disputed amount ${f['TransactionAmt']:.2f} matches "
                   f"{len(match)} prior charges to the same merchant domain — a recurring pattern",
                   f"query:recurring({ctx['customer_id']})", match["TransactionID"].tolist()[:5],
                   0.3, pattern="none")
    return None


# ===========================================================================
# DEFENDER probes: search for legitimate explanations and evidence that
# CONTRADICTS the fraud hypothesis (all return exculpatory LR < 1).
# ===========================================================================
def probe_familiar_device(ds, ctx):
    f = ctx["flagged"]
    prof = ctx["flagged_profile"]
    if f["channel"] != "online" or not prof:
        return None
    hits = ctx["hist"][ctx["hist"]["device_profile"] == prof]
    if hits.empty:
        return None
    span = (f["ts"] - hits["ts"].min()).days
    if span >= 60 or str(f.get("id_15")) == "Found":
        return _ev("familiar_device", f"This device has been used on the account for {max(span, 0)} "
                   f"days (marked '{f.get('id_15')}') — it is not new to the cardholder",
                   f"query:device_history({ctx['customer_id']})", [ctx["flagged_txn_id"]], 0.5)
    return None


def probe_familiar_merchant(ds, ctx):
    f, h = ctx["flagged"], ctx["hist"]
    dom = f.get("P_emaildomain")
    if dom is None or (isinstance(dom, float) and pd.isna(dom)):
        return None
    prior = h[(h["ts"] < f["ts"]) & (h["P_emaildomain"] == dom)]
    if len(prior) >= 2:
        return _ev("familiar_merchant", f"Purchaser domain {dom} was used on {len(prior)} prior "
                   f"transactions by this cardholder", f"query:merchant_history({ctx['customer_id']})",
                   prior["TransactionID"].tolist()[:5], 0.7)
    return None


def probe_consistent_amount(ds, ctx):
    f, hs, h = ctx["flagged"], ctx["histsum"], ctx["hist"]
    if hs["n"] < 5:
        return None
    lo = float(h["TransactionAmt"].quantile(0.05))
    if lo <= f["TransactionAmt"] <= hs["amt_p95"] and (pd.isna(f["ProductCD"]) or f["ProductCD"] in hs["products"]):
        return _ev("consistent_amount", f"Amount ${f['TransactionAmt']:.2f} and product sit within the "
                   f"cardholder's normal range (${lo:.0f}–${hs['amt_p95']:.0f})",
                   f"query:history({ctx['customer_id']})", [ctx["flagged_txn_id"]], 0.65)
    return None


def probe_clean_tenure(ds, ctx):
    f, hs, h = ctx["flagged"], ctx["histsum"], ctx["hist"]
    if hs["n"] < 40:
        return None
    tenure = (f["ts"] - h["ts"].min()).days
    ndev = h[h.device_profile != ""].device_profile.nunique()
    if tenure >= 120 and ndev <= 1:
        return _ev("clean_tenure", f"Long-tenured account ({tenure} days, {hs['n']} txns) on a single "
                   f"device with no prior fraud", f"query:profile({ctx['customer_id']})", [], 0.7)
    return None


def ds_cardid(ds, customer_id: str) -> str:
    """Best-effort card_id for a customer: exact from closed/case data, else -K1."""
    hit = ds.closed[ds.closed.customer_id == customer_id]
    if len(hit):
        return str(hit.iloc[0]["card_id"])
    hit = ds.case_pack[ds.case_pack.customer_id == customer_id]
    if len(hit):
        return str(hit.iloc[0]["card_id"])
    return f"{customer_id}-K1"
