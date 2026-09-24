"""
Narrative generation. The LLM ONLY explains what the deterministic engine already
decided - it never picks the action or invents evidence. Falls back to a template
when CLARA_LLM_PROVIDER=none (or no key), so the demo always runs offline.
"""
from __future__ import annotations

import os
import textwrap

from .schemas import Case


def fmt_value(raw) -> str:
    """Human-readable one-liner for an evidence value (summarizes lists like similar-cases)."""
    if isinstance(raw, list):
        if raw and isinstance(raw[0], dict) and "case_id" in raw[0]:
            return ", ".join(f"{r['case_id']}({r['outcome']},sim={r['similarity']})" for r in raw)
        return f"{len(raw)} items"
    return str(raw)


def _facts(case: Case) -> str:
    lines = [f"Trigger: {case.trigger}",
             f"Subject: card1={case.card1}",
             f"Prior (bank risk score): {case.prior_prob:.0%}",
             "Evidence gathered (in order):"]
    for i, e in enumerate(case.evidence, 1):
        lines.append(f"  {i}. {e.label} = {fmt_value(e.raw_value)} [{e.bucket}] "
                     f"-> {e.prob_before:.0%} to {e.prob_after:.0%} "
                     f"(chosen because: {e.rationale})")
    lines.append(f"Final confidence: {case.final_prob:.0%}")
    lines.append(f"Stop reason: {case.stop_reason}")
    lines.append(f"Typology: {case.typology}")
    if case.nba_final:
        lines.append(f"Recommended action: {case.nba_final.name} "
                     f"(approval: {case.nba_final.approval_route}) - {case.nba_final.reason}")
    if case.similar_cases:
        sc = ", ".join(f"{s['case_id']}({s['outcome']},sim={s['similarity']})"
                       for s in case.similar_cases)
        lines.append(f"Most similar past cases: {sc}")
    return "\n".join(lines)


def _template(case: Case) -> str:
    ev_bits = "; ".join(f"{e.label.lower()} was {fmt_value(e.raw_value)}" for e in case.evidence)
    act = case.nba_final
    return textwrap.dedent(f"""\
        Investigation {case.case_id}: started from '{case.trigger}' on card1={case.card1}
        at a prior fraud confidence of {case.prior_prob:.0%}. The agent gathered evidence
        by value-of-information: {ev_bits}. Confidence moved to {case.final_prob:.0%}.
        {case.stop_reason.capitalize()}. Assessed typology: {case.typology}.
        Recommended next action: {act.name} (approval route: {act.approval_route}) - {act.reason}.
        """).strip()


def narrate(case: Case) -> str:
    provider = os.getenv("CLARA_LLM_PROVIDER", "none").lower()
    facts = _facts(case)
    prompt = (
        "You are a fraud investigator writing a concise, defensible case summary for an "
        "analyst. Use ONLY the facts below. Explain what evidence was used, why extra "
        "evidence was (or was not) requested, the remaining uncertainty, and why the "
        "recommended action follows. 4-7 sentences, plain professional English.\n\n"
        f"{facts}\n"
    )
    try:
        if provider == "anthropic" and os.getenv("ANTHROPIC_API_KEY"):
            import anthropic
            client = anthropic.Anthropic()
            msg = client.messages.create(
                model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5"),
                max_tokens=400,
                messages=[{"role": "user", "content": prompt}],
            )
            return msg.content[0].text.strip()
        if provider == "openai" and os.getenv("OPENAI_API_KEY"):
            from openai import OpenAI
            client = OpenAI()
            r = client.chat.completions.create(
                model=os.getenv("OPENAI_MODEL", "gpt-4o"),
                max_tokens=400,
                messages=[{"role": "user", "content": prompt}],
            )
            return r.choices[0].message.content.strip()
    except Exception as exc:  # never let the LLM break the demo
        return _template(case) + f"\n\n[note: LLM narrative unavailable ({exc}); used template]"
    return _template(case)
