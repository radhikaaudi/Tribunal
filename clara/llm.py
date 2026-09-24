"""
Optional LLM layer (Claude). GraphRAG: the model receives the retrieved graph evidence, the
retrieved policy chunks and the similar prior cases — never raw tables — and writes the
analyst summary and a readable SAR narrative. It cannot change the verdict, the actions or
the routes: those are computed deterministically from the evidence and the policy.

Providers: CLARA_LLM_PROVIDER=anthropic (ANTHROPIC_API_KEY) or gemini (GEMINI_API_KEY, Google AI
Studio). Anything else -> no-op, template text.
"""
from __future__ import annotations

import json
import os


def _client():
    if os.getenv("CLARA_LLM_PROVIDER", "none").lower() != "anthropic" or not os.getenv("ANTHROPIC_API_KEY"):
        return None
    import anthropic
    return anthropic.Anthropic()


def provider() -> str:
    p = os.getenv("CLARA_LLM_PROVIDER", "none").lower()
    if p == "anthropic" and os.getenv("ANTHROPIC_API_KEY"):
        return "anthropic"
    if p == "gemini" and os.getenv("GEMINI_API_KEY"):
        return "gemini"
    return "none"


_last_call = [0.0]
_exhausted: set = set()


def _gemini(prompt: str) -> tuple[str, int]:
    """Gemini REST call with pacing, retry and model fallback. GEMINI_MODEL may be a comma list;
    a model whose free-tier daily quota is used up (or that stays overloaded) is skipped."""
    import time
    import requests
    models = [m.strip() for m in os.getenv("GEMINI_MODEL", "gemini-flash-lite-latest,gemini-3-flash-preview").split(",") if m.strip()]
    gap = float(os.getenv("GEMINI_MIN_GAP_S", "5"))
    last = None
    for model in [m for m in models if m not in _exhausted]:
        for attempt in range(3):
            wait = _last_call[0] + gap - time.time()
            if wait > 0:
                time.sleep(wait)
            _last_call[0] = time.time()
            r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                              headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"], "Content-Type": "application/json"},
                              json={"contents": [{"parts": [{"text": prompt}]}],
                                    "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json"}},
                              timeout=90)
            last = r
            if r.status_code == 200:
                j = r.json()
                text = "".join(p.get("text", "") for p in j["candidates"][0]["content"]["parts"])
                return text, int(j.get("usageMetadata", {}).get("totalTokenCount", 0))
            if r.status_code == 429 and "PerDay" in r.text:
                _exhausted.add(model)          # daily quota gone: next model
                break
            if r.status_code in (429, 500, 503):
                time.sleep(min(20, 4 * 2 ** attempt))
                continue
            break
    if last is not None:
        last.raise_for_status()
    raise RuntimeError("no Gemini model available")


def enrich(ans: dict, policy_chunks: list[dict]) -> int:
    prov = provider()
    if prov == "none":
        return 0
    c = _client() if prov == "anthropic" else None
    model = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5")
    case = ans["case"]
    context = {
        "trigger": ans.get("debate", {}),
        "verdict": case["verdict"], "fraud_probability": case["fraud_probability"],
        "pattern": case["pattern"], "exposure_usd": case["exposure_usd"],
        "evidence": case["evidence"], "evidence_requests": ans["evidence_requests"],
        "final_actions": ans["next_best_actions"]["final"],
        "similar_prior_cases": case["similar_prior_cases"],
        "policy": [{"section": p["section"], "text": p["text"]} for p in policy_chunks],
    }
    prompt = ("You are a bank fraud investigator's writing assistant. Using ONLY the facts in the JSON "
              "context, write a 2-5 sentence case summary an analyst can read: what triggered the case, "
              "the decisive evidence, what was assumed from the customer (if anything), the verdict and "
              "why the actions follow from the cited policy rules. Do not invent IDs, amounts or facts. "
              "Return JSON {\"summary\": str" + (", \"sar_narrative\": str (6-12 sentences, FinCEN 5W1H, "
              "keep every fact and ID from the draft, add none)" if ans["sar"]["file"] else "") + "}.\n\n"
              + json.dumps(context, default=str)
              + ("\n\nDRAFT SAR NARRATIVE:\n" + ans["sar"]["narrative"] if ans["sar"]["file"] else ""))
    if prov == "gemini":
        text, tokens = _gemini(prompt)
    else:
        m = c.messages.create(model=model, max_tokens=1200, messages=[{"role": "user", "content": prompt}])
        text, tokens = m.content[0].text, int(m.usage.input_tokens + m.usage.output_tokens)
    text = text.strip()
    try:
        out = json.loads(text[text.find("{"): text.rfind("}") + 1])
        if out.get("summary"):
            case["summary"] = out["summary"].strip()
        if ans["sar"]["file"] and out.get("sar_narrative"):
            ans["sar"]["narrative"] = out["sar_narrative"].strip()
    except Exception:
        pass
    return tokens
