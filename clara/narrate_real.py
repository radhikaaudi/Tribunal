"""
SAR narrative. Built only from facts the investigation established (FinCEN 5W1H: who,
what, when, where, how, why), so it always runs offline. When an LLM is configured,
clara.llm rewrites it for readability with an instruction to keep every fact and add none.
"""
from __future__ import annotations

PAT_TEXT = {
    "card_testing": "a run of very small online authorizations followed by larger purchases, consistent with "
                    "testing a stolen card number before use",
    "card_not_present_fraud": "card-not-present purchases inconsistent with the cardholder's history",
    "card_not_present_new_device": "card-not-present purchases from a device newly seen on the account",
    "out_of_region_use": "card-present purchases in a billing region the cardholder has no history in",
    "account_takeover": "mixed-channel activity with device and match-flag anomalies indicating stolen credentials",
}


def sar_narrative(ctx, fired, pattern, exposure, aff, connected_cards, connected_profiles, dates,
                  reqs, similar, final):
    who = f"customer {ctx['customer_id']}, card {ctx['card_id']}"
    when = (f"on {dates[0]}" if len(dates) == 2 and dates[0] == dates[1] else
            f"between {dates[0]} and {dates[1]}" if len(dates) == 2 else "on the alert date")
    n = len(aff)
    lines = []
    ids = ", ".join(str(t) for t in aff["TransactionID"].head(8)) if n else str(ctx["flagged_txn_id"])
    amts = ", ".join(f"${a:,.2f}" for a in aff["TransactionAmt"].head(8)) if n else ""
    lines.append(f"This report concerns {who}. {when[0].upper() + when[1:]}, {n or 1} transaction(s) "
                 f"({ids}) totalling ${exposure:,.2f} were identified as suspicious.")
    channels = sorted(set(aff["channel"])) if n else [ctx["flagged"]["channel"]]
    where = f"The activity was conducted {' and '.join(channels).replace('_', '-')}"
    regions = sorted({f"{a:.0f}" for a in aff["addr1"].dropna()}) if n else []
    if regions:
        where += f", billed to region(s) {', '.join(regions)}"
    lines.append(where + (f", with amounts of {amts}." if amts else "."))

    if "structuring" in fired:
        v = fired["structuring"]["value"]
        lines.append(f"The {v['n']} purchases were made within {v['minutes']:.0f} minutes and each was just under "
                     f"$500, indicating amounts deliberately sized to stay below a $500 authorization threshold.")
        if v["profiles"]:
            lines.append(f"They came from {len(v['profiles'])} device profile(s): {'; '.join(v['profiles'][:3])}.")
    elif "shared_device" in fired:
        v = fired["shared_device"]["value"]
        lines.append(f"The transactions came from the device profile '{v['profile']}', which was used by "
                     f"{v['n']} other customers between {v['window'][0]} and {v['window'][1]}, "
                     f"{v['proxy_share']:.0%} of the time behind an anonymous proxy and {v['new_share']:.0%} "
                     f"marked as a new device.")
        if connected_cards:
            lines.append(f"Other cards on this device include {', '.join(connected_cards[:10])}"
                         f"{' and others' if len(connected_cards) > 10 else ''}, indicating a common actor "
                         f"across unrelated cardholders.")
    else:
        lines.append(f"The pattern observed is {PAT_TEXT.get(pattern, 'suspicious activity')}.")
    lk = fired.get("link_known_fraud")
    if lk and lk["value"]["cases"]:
        lines.append(f"The same device profile appears on previously confirmed fraud cases "
                     f"{', '.join(lk['value']['cases'][:4])}.")
    for key in ("card_testing", "new_device", "flagged_anomaly", "cnp_burst", "out_of_region"):
        if key in fired and fired[key]["lr"] > 1 and key not in ("structuring",):
            lines.append(f"Supporting detail: {fired[key]['claim']}.")
            break
    if reqs:
        lines.append(f"When contacted ({reqs[0]['type'].replace('_', ' ')}), the cardholder's response was: "
                     f"{reqs[0]['assumed_response'].split(' (assumed')[0].lower()}.")
    elif ctx["trigger_type"] == "customer_report":
        lines.append("The cardholder reported the transaction as unauthorized.")
    lines.append(f"The activity is suspicious because it is inconsistent with the cardholder's established "
                 f"behaviour and matches "
                 + ("a coordinated pattern not covered by the bank's documented typologies"
                    if pattern == "undocumented" else f"the known typology '{pattern}'")
                 + (f", as in prior confirmed cases {', '.join(m['case_id'] for m in similar[:2])}" if similar else "")
                 + ".")
    acts = ", ".join(a["action"] for a in final)
    lines.append(f"Recommended actions: {acts}. Total suspicious amount: ${exposure:,.2f}.")
    return " ".join(lines)
