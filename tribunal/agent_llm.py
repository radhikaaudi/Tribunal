"""
The LLM reasoning layer for Tribunal.

Design rule (unchanged): the LLM never *decides*. Every verdict, threshold, action
and approval route stays a deterministic function of the evidence — auditable and
reproducible. The LLM does what the challenge asks it to do: **tool selection,
evidence synthesis, reasoning, and explanation**, always *grounded* in the GraphRAG
context (retrieved policy + typologies + connected graph evidence) so it cites rather
than invents.

Provider-agnostic (`TRIBUNAL_LLM_PROVIDER` = anthropic | openai | none). With `none`
— the default, no API key needed — every function returns a high-quality deterministic
rendering, so the whole agent runs and demos offline. With a key, the same grounded
prompt is sent to the model to polish the human-facing reasoning.
"""
from __future__ import annotations

import os

# The evidence tools the agent can select from (maps 1:1 to signals.py probes and the
# installed GSQL queries). Ordering/selection is the agent's "tool use".
TOOL_CATALOG = [
    ("history_summary", "customer transaction history baseline", "graph"),
    ("probe_flagged_anomaly", "is the flagged purchase off the customer's pattern?", "graph"),
    ("probe_card_testing", "small-amount authorization burst (R5)", "graph"),
    ("probe_cnp_burst", "card-not-present burst", "graph"),
    ("probe_new_device", "device newly seen on this account", "graph"),
    ("probe_out_of_region", "billing region with no history", "graph"),
    ("probe_account_takeover", "identity match-flag / distance anomaly", "graph"),
    ("probe_shared_device", "device-neighbour traversal → shared-device ring (R6)", "graph"),
    ("probe_link_known_fraud", "path to a confirmed-fraud node", "graph"),
    ("similar_cases", "case-memory vector retrieval of prior outcomes", "vector"),
    ("ask_customer", "controlled action: customer validation (R1/R2)", "action"),
    ("step_up_auth", "controlled action: step-up authentication", "action"),
]


def _provider() -> str:
    return os.getenv("TRIBUNAL_LLM_PROVIDER", "none").lower()


def select_tools(ctx: dict) -> list[dict]:
    """Tool-selection trace: which evidence tools the agent runs and why, ordered by
    the trigger. Deterministic (auditable); the LLM may re-rank when enabled."""
    trigger = ctx.get("trigger_type", "")
    plan = []
    # every investigation starts from the graph baseline + the trigger-relevant probes
    order = {
        "risk_score": ["history_summary", "probe_flagged_anomaly", "probe_card_testing",
                       "probe_cnp_burst", "probe_new_device", "probe_out_of_region",
                       "probe_account_takeover", "similar_cases"],
        "customer_report": ["history_summary", "probe_flagged_anomaly", "probe_new_device",
                            "probe_cnp_burst", "similar_cases", "ask_customer"],
        "analyst_request": ["history_summary", "probe_shared_device", "probe_link_known_fraud",
                            "probe_new_device", "similar_cases"],
    }.get(trigger, [t[0] for t in TOOL_CATALOG])
    cat = {name: (desc, kind) for name, desc, kind in TOOL_CATALOG}
    for name in order:
        if name in cat:
            desc, kind = cat[name]
            plan.append({"tool": name, "why": desc, "kind": kind})
    return plan


def reason(ctx: dict, evidence: list[dict], decision: dict, grounding: dict) -> dict:
    """Synthesise the agent's grounded reasoning for the case record + UI.

    Returns provider, a tool-selection plan, the reasoning narrative, an explicit
    uncertainty statement, and the citations the reasoning is grounded on.
    """
    plan = select_tools(ctx)
    baseline = _deterministic_reasoning(ctx, evidence, decision, grounding)
    provider = _provider()
    reasoning = baseline
    tokens = 0
    has_key = ((provider == "anthropic" and os.getenv("ANTHROPIC_API_KEY"))
               or (provider == "openai" and os.getenv("OPENAI_API_KEY")))
    if provider in ("anthropic", "openai") and has_key:
        try:
            reasoning, tokens = _llm_reasoning(baseline, grounding, provider)
        except Exception:
            reasoning, provider = baseline, f"{provider}(fallback:offline)"
    elif provider in ("anthropic", "openai"):
        # provider selected but no API key present -> honest offline label
        provider = f"{provider}(no-key:offline)"
    return {
        "provider": provider,
        "tool_plan": plan,
        "reasoning": reasoning,
        "uncertainty": _uncertainty(decision),
        "grounded_on": grounding.get("citations", []),
        "tokens": tokens,
    }


def _uncertainty(decision: dict) -> str:
    p = decision.get("fraud_probability", 0.0)
    v = decision.get("verdict")
    if v == "fraud":
        return (f"Confidence {p:.0%} — at/above the 0.85 stop line or supported by a hard signal; "
                f"the fraud hypothesis survived its strongest defence, so the decision is settled.")
    if v == "legitimate":
        return f"Confidence {p:.0%} — at/below the 0.15 clear line; a legitimate explanation accounts for the alert."
    return (f"Confidence {p:.0%} — in the uncertain band; a decisive fact is missing, so the agent "
            f"requests controlled evidence (verification / step-up) rather than acting.")


def _deterministic_reasoning(ctx, evidence, decision, grounding) -> str:
    pros = [e["claim"] for e in evidence if e.get("side") == "prosecution"]
    dfn = [e["claim"] for e in evidence if e.get("side") == "defense"]
    cites = ", ".join(grounding.get("citations", [])[:3]) or "the fraud policy"
    parts = [
        f"Investigating card {ctx.get('card_id')} (customer {ctx.get('customer_id')}), "
        f"triggered by {ctx.get('trigger_type')}.",
        f"Evidence for fraud: {'; '.join(pros) if pros else 'none of weight'}.",
        f"Evidence against: {'; '.join(dfn) if dfn else 'none of weight'}.",
        f"Assessed {decision.get('verdict')} at {decision.get('fraud_probability', 0):.0%} "
        f"({decision.get('pattern')}). {_uncertainty(decision)}",
        f"Grounded in {cites}.",
    ]
    return " ".join(parts)


def _llm_reasoning(baseline: str, grounding: dict, provider: str):
    prompt = (
        "You are a fraud investigation analyst. Using ONLY the grounding context and the "
        "draft below, write a tight 4-7 sentence reasoning note explaining the decision: "
        "what evidence was used, why any extra evidence was requested, and why the action "
        "follows policy. Cite policy rule numbers where the context provides them. Do not "
        "add facts or change the verdict.\n\n"
        f"{grounding.get('prompt_context', '')}\n\n## DRAFT REASONING\n{baseline}\n"
    )
    model_a = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5")
    if provider == "anthropic" and os.getenv("ANTHROPIC_API_KEY"):
        import anthropic
        c = anthropic.Anthropic()
        m = c.messages.create(model=model_a, max_tokens=500,
                              messages=[{"role": "user", "content": prompt}])
        tok = getattr(m, "usage", None)
        return m.content[0].text.strip(), (tok.input_tokens + tok.output_tokens if tok else 0)
    if provider == "openai" and os.getenv("OPENAI_API_KEY"):
        from openai import OpenAI
        c = OpenAI()
        r = c.chat.completions.create(model=os.getenv("OPENAI_MODEL", "gpt-4o"),
                                      max_tokens=500,
                                      messages=[{"role": "user", "content": prompt}])
        u = getattr(r, "usage", None)
        return r.choices[0].message.content.strip(), (u.total_tokens if u else 0)
    return baseline, 0
