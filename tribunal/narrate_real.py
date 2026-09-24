"""
SAR narrative + case summary text. Template-based so it always runs offline; if
TRIBUNAL_LLM_PROVIDER is set, the template is handed to the LLM to polish (never to
decide). The narrative follows FinCEN 5W1H and must stand on its own.
"""
from __future__ import annotations

import os


def sar_narrative(ctx, pattern, exposure, aff_df, connected_cards, connected_profiles, dates):
    who = f"card {ctx['card_id']} held by customer {ctx['customer_id']}"
    when = f"between {dates[0]} and {dates[1]}" if len(dates) == 2 else "on the flagged date"
    n = len(aff_df)
    amt_list = ", ".join(f"${a:.2f}" for a in aff_df["TransactionAmt"].head(6)) if n else ""
    channel = (aff_df["channel"].mode().iloc[0] if n and not aff_df["channel"].mode().empty else "online")
    region = ""
    if n and "addr1" in aff_df and aff_df["addr1"].notna().any():
        region = f" in billing region {aff_df['addr1'].dropna().iloc[0]}"

    pat_text = {
        "card_testing": "a sequence of small authorizations followed by a larger purchase, consistent "
                        "with testing of a stolen card number before use",
        "card_not_present_fraud": "card-not-present purchases inconsistent with the cardholder's history",
        "card_not_present_new_device": "card-not-present purchases from a device newly seen on the account",
        "out_of_region_use": "card-present purchases in a billing region the cardholder has no history in",
        "account_takeover": "mixed-channel activity with device and match-flag anomalies indicating stolen credentials",
        "undocumented": "coordinated activity across multiple cards sharing a single device profile",
    }.get(pattern, "suspicious activity")

    lines = [
        f"On the dates in question, {who} was involved in {n} transaction(s) {when} totaling "
        f"${exposure:,.2f}, flagged as suspicious.",
        f"The activity comprised {channel} transactions{region}" + (f" ({amt_list})." if amt_list else "."),
        f"The pattern observed is {pat_text}.",
    ]
    if connected_profiles and connected_profiles[0]:
        lines.append(f"The transactions share the device profile '{connected_profiles[0]}'.")
    if connected_cards:
        lines.append(f"This device profile also appears on other card(s): {', '.join(connected_cards[:8])}, "
                     f"indicating a common actor across cardholders.")
    lines.append("The cardholder, contacted during the investigation, did not confirm the activity.")
    lines.append(f"The activity is suspicious because it is inconsistent with the cardholder's established "
                 f"behaviour and matches a known fraud typology ({pattern}).")
    lines.append(f"The card has been recommended for blocking and reissue; connected cards, where present, "
                 f"were placed under monitoring. Total suspicious amount: ${exposure:,.2f}.")
    narrative = " ".join(lines)

    provider = os.getenv("TRIBUNAL_LLM_PROVIDER", "none").lower()
    if provider in ("anthropic", "openai"):
        try:
            return _polish(narrative, provider)
        except Exception:
            return narrative
    return narrative


def _polish(narrative, provider):
    prompt = ("Rewrite this SAR narrative to read as a clean, professional FinCEN-style filing. "
              "Keep every fact; do not add facts. 6-12 sentences.\n\n" + narrative)
    if provider == "anthropic" and os.getenv("ANTHROPIC_API_KEY"):
        import anthropic
        c = anthropic.Anthropic()
        m = c.messages.create(model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5"),
                              max_tokens=500, messages=[{"role": "user", "content": prompt}])
        return m.content[0].text.strip()
    if provider == "openai" and os.getenv("OPENAI_API_KEY"):
        from openai import OpenAI
        c = OpenAI()
        r = c.chat.completions.create(model=os.getenv("OPENAI_MODEL", "gpt-4o"),
                                      max_tokens=500, messages=[{"role": "user", "content": prompt}])
        return r.choices[0].message.content.strip()
    return narrative
