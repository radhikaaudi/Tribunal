"""
Evidence probes over the real dataset. Each returns a structured evidence dict
(claim/source/ref/entity_ids) plus a likelihood ratio and the pattern it supports,
or None if it doesn't fire. The investigator turns these into belief updates + the
case's evidence list.

Every probe is anchored on the flagged transaction's time: behaviour months away from
the alert is not evidence about the alert. Note that one `customer_id` in this dataset
aggregates a card-issuer group (some have thousands of transactions), so "history"
probes compare against that group's behaviour and are deliberately weak.
"""
from __future__ import annotations

import pandas as pd

HOUR = pd.Timedelta(hours=1)
DAY = pd.Timedelta(days=1)
# free-mail providers are shared by millions of people: never a "merchant" or a link
FREE_MAIL = {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "aol.com", "anonymous.com",
             "icloud.com", "live.com", "msn.com", "comcast.net", "ymail.com", "me.com",
             "att.net", "verizon.net", "sbcglobal.net", "bellsouth.net", "hotmail.es",
             "yahoo.com.mx", "outlook.es", "gmail"}
RING_MIN = 10          # distinct other customers on one specific profile within 30 days = device farm


def _ev(key, claim, ref, entity_ids, lr, pattern=None, source="graph", value=None):
    return {"key": key, "claim": claim, "source": source, "ref": ref,
            "entity_ids": [str(e) for e in entity_ids], "lr": lr,
            "pattern": pattern, "value": value}


def _around(h, t, before, after):
    return h[(h["ts"] >= t - before) & (h["ts"] <= t + after)]


def history_summary(ds, cust, before=None):
    h = ds.customer_txns(cust)
    if before is not None:
        h = h[h["ts"] < before]
    if h.empty:
        return {"n": 0, "regions": set(), "amt_p95": 0, "amt_med": 0, "products": set(),
                "channels": set(), "emails": set(), "online_per_day": 0.0}
    days = max((h["ts"].max() - h["ts"].min()).days, 1)
    return {
        "n": len(h),
        "regions": set(h["addr1"].dropna().tolist()),
        "amt_p95": float(h["TransactionAmt"].quantile(0.95)),
        "amt_med": float(h["TransactionAmt"].median()),
        "products": set(h["ProductCD"].dropna().tolist()),
        "channels": set(h["channel"].dropna().tolist()),
        "emails": set(h["P_emaildomain"].dropna().tolist()),
        "online_per_day": float((h["channel"] == "online").sum()) / days,
    }


# ===========================================================================
# PROSECUTOR probes: evidence FOR the fraud hypothesis (LR > 1)
# ===========================================================================
def probe_flagged_anomaly(ds, ctx):
    f, hist = ctx["flagged"], ctx["histsum"]
    if hist["n"] <= 1:
        return None
    unusual = []
    if f["TransactionAmt"] > max(hist["amt_p95"], 1) * 1.2:
        unusual.append(f"amount ${f['TransactionAmt']:.2f} exceeds the customer's 95th-pct ${hist['amt_p95']:.2f}")
    if pd.notna(f["ProductCD"]) and f["ProductCD"] not in hist["products"]:
        unusual.append(f"product code {f['ProductCD']} never used by this customer before")
    if not unusual:
        return None   # "consistent" is carried by probe_consistent_amount; don't count it twice
    return _ev("flagged_anomaly", "Flagged transaction is unusual: " + "; ".join(unusual),
               f"query:customer_history({ctx['customer_id']})", [ctx["flagged_txn_id"]], 2.0,
               pattern="card_not_present_fraud")


def probe_card_testing(ds, ctx):
    """Pattern 1 / R5: >=3 online authorizations under $5 within an hour, then a larger
    purchase, in the day leading up to (or containing) the flagged transaction."""
    f, h = ctx["flagged"], ctx["hist"]
    on = _around(h[h["channel"] == "online"], f["ts"], 24 * HOUR, 6 * HOUR).sort_values("ts")
    small = on[on["TransactionAmt"] < 5.0]
    for i in range(len(small)):
        t0 = small.iloc[i]["ts"]
        w = small[(small["ts"] >= t0) & (small["ts"] <= t0 + HOUR)]
        if len(w) < 3:
            continue
        after = on[(on["ts"] > w.iloc[-1]["ts"]) & (on["ts"] <= w.iloc[-1]["ts"] + 6 * HOUR)
                   & (on["TransactionAmt"] > 50)]
        if after.empty:
            continue
        # the sequence must reach the flagged transaction, or it is a different episode
        if not (w["TransactionID"].eq(ctx["flagged_txn_id"]).any()
                or after["TransactionID"].eq(ctx["flagged_txn_id"]).any()):
            continue
        ids = w["TransactionID"].tolist() + after["TransactionID"].tolist()
        cleared = after["TransactionAmt"].max() > 100
        claim = (f"{len(w)} online authorizations under $5 within an hour "
                 f"({', '.join(f'${a:.2f}' for a in w['TransactionAmt'])}), then "
                 f"{len(after)} larger purchase(s) up to ${after['TransactionAmt'].max():.2f}")
        return _ev("card_testing", claim, f"query:card_window({ctx['card_id']},1h)",
                   ids, 9.0 if cleared else 5.0, pattern="card_testing",
                   value={"cleared_over_100": bool(cleared)})
    return None


def probe_structuring(ds, ctx):
    """Undocumented pattern seen in closed cases CC-3748/3841/3907/4086/4124: several online
    purchases within ~40 minutes, each just under a $500 authorization threshold."""
    f, h = ctx["flagged"], ctx["hist"]
    if f["channel"] != "online":
        return None
    w = _around(h[h["channel"] == "online"], f["ts"], HOUR, HOUR)
    near = w[(w["TransactionAmt"] >= 400) & (w["TransactionAmt"] < 500)].sort_values("ts")
    if len(near) < 3 or not near["TransactionID"].eq(ctx["flagged_txn_id"]).any():
        return None
    span = (near["ts"].max() - near["ts"].min()).total_seconds() / 60
    profiles = sorted({p for p in near["device_profile"] if p})
    claim = (f"{len(near)} online purchases in {span:.0f} minutes, each just under $500 "
             f"({', '.join(f'${a:.2f}' for a in near['TransactionAmt'])}) from "
             f"{len(profiles)} device profile(s) — amounts appear chosen to stay under a $500 "
             f"authorization threshold")
    return _ev("structuring", claim, f"query:card_window({ctx['card_id']},1h)",
               near["TransactionID"].tolist(), 8.0, pattern="undocumented",
               value={"n": len(near), "minutes": span, "profiles": profiles,
                      "kind": "threshold_structuring"})


def probe_cnp_burst(ds, ctx):
    """Pattern 2: two to four card-not-present purchases within 48h that look alike.
    For high-volume (aggregated) customers only near-identical repeat charges count."""
    f, h = ctx["flagged"], ctx["hist"]
    if f["channel"] != "online":
        return None
    w = _around(h[h["channel"] == "online"], f["ts"], 48 * HOUR, 48 * HOUR)
    others = w[w["TransactionID"] != ctx["flagged_txn_id"]]
    amt = float(f["TransactionAmt"])
    same = others[(others["TransactionAmt"] - amt).abs() <= max(0.02 * amt, 0.5)]
    same = same[(same["ts"] - f["ts"]).abs() <= 2 * HOUR]
    prior = h[(h["ts"] < f["ts"] - 2 * DAY) & (h["ProductCD"] == f["ProductCD"])]
    habitual = ((prior["TransactionAmt"] - amt).abs() <= max(0.02 * amt, 0.5)).sum() >= 3
    if habitual:
        return None   # repeat purchases of an amount this customer buys routinely are not a burst
    if 1 <= len(same) <= 3:
        ids = sorted(same["TransactionID"].tolist() + [ctx["flagged_txn_id"]])
        return _ev("cnp_burst", f"{len(ids)} near-identical online charges (~${amt:.2f}) within two hours",
                   f"query:card_window({ctx['card_id']},48h)", ids, 2.0,
                   pattern="card_not_present_fraud")
    if ctx["histsum"]["online_per_day"] <= 2 and 2 <= len(w) <= 4:
        return _ev("cnp_burst", f"{len(w)} online purchases within a 48h window on a customer who "
                   f"normally makes ~{ctx['histsum']['online_per_day']:.1f} per day",
                   f"query:card_window({ctx['card_id']},48h)", w["TransactionID"].tolist(), 1.6,
                   pattern="card_not_present_fraud")
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
        bits.append("device marked New for this account (id_15)")
    if proxy:
        bits.append(f"behind a proxy ({f.get('id_23')})")
    # 'New' alone is weak (~43% of online txns; people buy new phones). Proxy is rarer/stronger.
    lr = 2.6 if (new and proxy) else 2.2 if proxy else 1.3
    return _ev("new_device", "Flagged transaction came from a " + " and ".join(bits)
               if new else "Flagged transaction was made " + bits[0],
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
    near = h[(h["ts"] >= f["ts"] - 2 * DAY) & (h["ts"] <= f["ts"] + 2 * DAY) & (h["addr1"].isin(home))]
    concurrent_home = len(near) > 0
    if region_days >= 3 and not concurrent_home:
        return _ev("out_of_region", f"Several days of activity in region {f['addr1']:.0f} with no "
                   f"concurrent home activity — consistent with travel, not a clone",
                   f"query:region_history({ctx['customer_id']})", [ctx["flagged_txn_id"]], 0.6)
    claim = f"Card-present use in billing region {f['addr1']:.0f} where the customer has no history"
    if concurrent_home:
        claim += " while home-region activity continues — the card cannot be in two places at once"
    return _ev("out_of_region", claim, f"query:region_history({ctx['customer_id']})",
               [ctx["flagged_txn_id"]], 3.2 if concurrent_home else 2.0, pattern="out_of_region_use")


def probe_account_takeover(ds, ctx):
    f, hist = ctx["flagged"], ctx["histsum"]
    if len(hist["channels"]) < 2:
        return None
    if int(f.get("f_flags") or 0) >= 3 and str(f.get("id_15")) == "New":
        return _ev("account_takeover", "Mixed-channel activity with match-flag anomalies (M1-M9 = F) "
                   "and a new device, consistent with stolen credentials rather than a stolen number",
                   f"query:identity({ctx['flagged_txn_id']})", [ctx["flagged_txn_id"]], 3.0,
                   pattern="account_takeover")
    return None


def probe_channel_mismatch(ds, ctx):
    """Pattern 5 (account takeover) via case memory: an online purchase on a customer who
    otherwise transacts in person, where the earlier online activity was itself confirmed fraud."""
    f, h = ctx["flagged"], ctx["hist"]
    if f["channel"] != "online":
        return None
    prior = h[h["ts"] < f["ts"]]
    on = prior[prior["channel"] == "online"]
    inp = prior[prior["channel"] == "in_person"]
    if len(inp) < 20 or len(on) > 0.25 * len(prior):
        return None
    fraud_on = on[on["TransactionID"].isin(ds.fraud_case_by_txn.keys())]
    own_on = len(on) - len(fraud_on)
    if own_on > 3:
        return None
    cases = sorted({ds.fraud_case_by_txn[t] for t in fraud_on["TransactionID"]})
    claim = (f"Customer transacts in person ({len(inp)} card-present transactions); of {len(on)} earlier online "
             f"transaction(s), {len(fraud_on)} belong to confirmed-fraud cases"
             + (f" ({', '.join(cases[:4])})" if cases else "")
             + " — online use is not this cardholder's own behaviour")
    return _ev("channel_mismatch", claim, f"query:channel_history({ctx['customer_id']})",
               [ctx["flagged_txn_id"]] + cases[:4], 3.0 if len(fraud_on) else 2.0, pattern="account_takeover")


def probe_shared_device(ds, ctx):
    """R6 / device-neighbour traversal: other customers on the same *specific* device profile
    within 30 days of the alert. Generic OS fingerprints never link accounts."""
    prof = ctx["flagged_profile"]
    if not ds.is_specific_profile(prof):
        return None
    f = ctx["flagged"]
    win = _around(ds.same_device(prof), f["ts"], 30 * DAY, 30 * DAY)
    others = sorted(set(win["customer_id"]) - {ctx["customer_id"]})
    if not others:
        return None
    mine = win[win["customer_id"] == ctx["customer_id"]].sort_values("ts")
    cards = [ds_cardid(ds, c) for c in others]
    proxy_share = float(win["id_23"].astype(str).str.contains("PROXY").mean())
    new_share = float((win["id_15"].astype(str) == "New").mean())
    strength = (6.0 if len(others) >= RING_MIN else 4.0 if len(others) >= 3 else 2.0)
    extra = []
    if proxy_share >= 0.5:
        extra.append(f"{proxy_share:.0%} behind a proxy")
    if new_share >= 0.5:
        extra.append(f"{new_share:.0%} marked New")
    claim = (f"Specific device profile '{prof}' used by {len(others)} other customer(s) within 30 days "
             f"({', '.join(cards[:6])}{'...' if len(cards) > 6 else ''})"
             + (f"; {', '.join(extra)}" if extra else "")
             + f"; this customer used it on {len(mine)} transaction(s)")
    return _ev("shared_device", claim, "query:device_neighbors(profile, ±30d)", cards[:25], strength,
               value={"connected_cards": cards, "profile": prof, "n": len(others),
                      "customer_txns": [int(t) for t in mine["TransactionID"]],
                      "window": [str(win["ts"].min().date()), str(win["ts"].max().date())],
                      "proxy_share": proxy_share, "new_share": new_share})


def probe_link_known_fraud(ds, ctx):
    """Path-to-known-fraud: the device profile appears on confirmed-fraud closed cases, or a
    device neighbour (±60 days) is a customer with confirmed fraud."""
    prof = ctx["flagged_profile"]
    if not ds.is_specific_profile(prof):
        return None
    cases = ds.closed_cases_on_profile(prof)
    peers = _around(ds.same_device(prof), ctx["flagged"]["ts"], 60 * DAY, 60 * DAY)
    fraud_peers = sorted((set(peers["customer_id"]) - {ctx["customer_id"]}) & ds.fraud_customers)
    reasons, ids = [], []
    if cases:
        reasons.append(f"Device profile appears on {len(cases)} confirmed-fraud closed case(s) "
                       f"({', '.join(cases[:4])})")
        ids += cases[:6]
    if fraud_peers:
        reasons.append(f"device neighbours include {len(fraud_peers)} customer(s) with confirmed "
                       f"fraud history ({', '.join(fraud_peers[:4])})")
        ids += fraud_peers[:6]
    if not reasons:
        return None
    return _ev("link_known_fraud", "; ".join(reasons), "query:link_to_known_fraud(profile)",
               ids, 5.0 if cases else 2.5, value={"cases": cases, "fraud_peers": fraud_peers})


# ===========================================================================
# DEFENDER probes: legitimate explanations that CONTRADICT the fraud hypothesis (LR < 1)
# ===========================================================================
def probe_recurring_match(ds, ctx):
    """R7: the disputed charge matches the customer's own recurring pattern — same merchant
    proxy (product + non-free-mail purchaser/recipient domain, or same region for card-present),
    same amount, roughly monthly."""
    f, h = ctx["flagged"], ctx["hist"]
    prior = h[h["ts"] < f["ts"] - 20 * DAY]
    amt = float(f["TransactionAmt"])
    m = prior[((prior["TransactionAmt"] - amt).abs() <= 0.10) & (prior["ProductCD"] == f["ProductCD"])]
    if f["channel"] == "online":
        dom = f.get("R_emaildomain") if pd.notna(f.get("R_emaildomain")) else f.get("P_emaildomain")
        if pd.isna(dom) or dom in FREE_MAIL:
            return None
        m = m[(m["R_emaildomain"] == dom) | (m["P_emaildomain"] == dom)]
    else:
        # card-present: no merchant field exists, so region is only a weak proxy; on a large
        # aggregated customer an amount/region/day match happens by chance - don't claim R7
        if ctx["histsum"]["n"] > 500:
            return None
        m = m[m["addr1"] == f["addr1"]]
    # monthly: same day-of-month (±3) in distinct earlier months
    m = m[(m["ts"].dt.day - f["ts"].day).abs() <= 3]
    months = m["ts"].dt.to_period("M").nunique()
    if months >= 3:
        return _ev("recurring_match", f"Disputed amount ${amt:.2f} matches {len(m)} prior charges with "
                   f"the same product/merchant proxy on the same day of the month in {months} earlier "
                   f"months — a recurring payment", f"query:recurring({ctx['customer_id']})",
                   m["TransactionID"].tolist()[:5], 0.2, pattern="none")
    return None


def probe_familiar_device(ds, ctx):
    f = ctx["flagged"]
    prof = ctx["flagged_profile"]
    if f["channel"] != "online" or not prof or not ds.is_specific_profile(prof):
        return None
    prior = ctx["hist"][ctx["hist"]["ts"] < f["ts"]]
    hits = prior[prior["device_profile"] == prof]
    if hits.empty:
        return None
    span = (f["ts"] - hits["ts"].min()).days
    if span >= 30:
        return _ev("familiar_device", f"This specific device has been used on the account for {span} "
                   f"days ({len(hits)} earlier transactions) — it is not new to the cardholder",
                   f"query:device_history({ctx['customer_id']})", hits["TransactionID"].tolist()[:5], 0.5)
    return None


def probe_familiar_merchant(ds, ctx):
    f, h = ctx["flagged"], ctx["hist"]
    dom = f.get("R_emaildomain") if pd.notna(f.get("R_emaildomain")) else f.get("P_emaildomain")
    if dom is None or (isinstance(dom, float) and pd.isna(dom)) or dom in FREE_MAIL:
        return None
    prior = h[(h["ts"] < f["ts"]) & ((h["P_emaildomain"] == dom) | (h["R_emaildomain"] == dom))]
    if len(prior) >= 2:
        return _ev("familiar_merchant", f"Email domain {dom} (not a free-mail provider) appears on "
                   f"{len(prior)} earlier transactions by this customer", f"query:email_history({ctx['customer_id']})",
                   prior["TransactionID"].tolist()[:5], 0.7)
    return None


def probe_consistent_amount(ds, ctx):
    f, hs, h = ctx["flagged"], ctx["histsum"], ctx["hist"]
    if hs["n"] < 5:
        return None
    prior = h[h["ts"] < f["ts"]]
    lo = float(prior["TransactionAmt"].quantile(0.05))
    if lo <= f["TransactionAmt"] <= hs["amt_p95"] and (pd.isna(f["ProductCD"]) or f["ProductCD"] in hs["products"]):
        return _ev("consistent_amount", f"Amount ${f['TransactionAmt']:.2f} and product {f['ProductCD']} sit "
                   f"within the customer's normal range (${lo:.0f}–${hs['amt_p95']:.0f})",
                   f"query:customer_history({ctx['customer_id']})", [ctx["flagged_txn_id"]], 0.7)
    return None


def probe_clean_tenure(ds, ctx):
    f, hs, h = ctx["flagged"], ctx["histsum"], ctx["hist"]
    if hs["n"] < 40:
        return None
    prior = h[h["ts"] < f["ts"]]
    tenure = (f["ts"] - prior["ts"].min()).days if len(prior) else 0
    ndev = prior[prior.device_profile != ""].device_profile.nunique()
    if tenure >= 120 and ndev <= 1 and not ds.customer_cases(ctx["customer_id"]):
        return _ev("clean_tenure", f"Long-tenured account ({tenure} days, {hs['n']} txns) on a single "
                   f"device with no prior fraud cases", f"query:customer_profile({ctx['customer_id']})", [], 0.7)
    return None


# ===========================================================================
# CONTEXT: case memory for this customer (does not move the belief on its own)
# ===========================================================================
def probe_case_memory(ds, ctx):
    mem = ds.customer_cases(ctx["customer_id"], before=ctx["flagged"]["ts"])
    if not mem:
        return None
    fr = [m for m in mem if m["outcome"] == "confirmed_fraud"]
    cl = [m for m in mem if m["outcome"] == "cleared"]
    pats = pd.Series([m["pattern"] for m in fr]).value_counts().to_dict() if fr else {}
    claim = (f"Customer has {len(mem)} closed case(s) on file: {len(fr)} confirmed fraud "
             f"({', '.join(f'{k} x{v}' for k, v in pats.items()) or 'none'}), {len(cl)} cleared")
    recent = sorted(mem, key=lambda m: m["opened_at"])[-3:]
    return _ev("case_memory", claim, f"query:prior_cases_for_customer({ctx['customer_id']})",
               [m["case_id"] for m in recent], 1.0,
               value={"fraud_patterns": pats, "n_fraud": len(fr), "n_cleared": len(cl),
                      "cleared_reasons": [m["cleared_reason"] for m in cl]})


def ds_cardid(ds, customer_id: str) -> str:
    """Best-effort card_id for a customer: exact from closed/case data, else -K1."""
    hit = ds.closed[ds.closed.customer_id == customer_id]
    if len(hit):
        return str(hit.iloc[0]["card_id"])
    hit = ds.case_pack[ds.case_pack.customer_id == customer_id]
    if len(hit):
        return str(hit.iloc[0]["card_id"])
    return f"{customer_id}-K1"
