"""
Fill in the LLM-written summary / SAR prose for answer files whose LLM call failed
(tokens == 0), e.g. after a free-tier rate limit. Only the narration is regenerated: the
verdict, evidence, actions, routes and SAR decision in the file are left untouched.

  python narrate_missing.py
"""
import glob
import json
import os
import re

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

from clara import knowledge as K  # noqa: E402
from clara import llm  # noqa: E402


def main():
    if llm.provider() == "none":
        raise SystemExit("no LLM configured (CLARA_LLM_PROVIDER / API key)")
    for fn in sorted(glob.glob("cases/HHG-*.json")):
        ans = json.load(open(fn))
        if ans.get("tokens"):
            continue
        acts = ans["next_best_actions"]["initial"] + ans["next_best_actions"]["final"]
        rules = []
        for a in acts:
            for r in re.findall(r"\b(R\d+|3a)\b", a["reason"]):
                if r not in rules:
                    rules.append(r)
        try:
            tokens = llm.enrich(ans, [K.get(r) for r in rules if K.get(r)])
        except Exception as e:
            print(f"{ans['case_id']}: still failing ({str(e)[:100]})")
            continue
        if tokens:
            ans["tokens"] = tokens
            ans["tool_calls"] += 1
            with open(fn, "w") as f:
                json.dump(ans, f, indent=2, default=str)
        print(f"{ans['case_id']}: {tokens} tokens")


if __name__ == "__main__":
    main()
