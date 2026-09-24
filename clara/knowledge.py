"""
Document side of GraphRAG: the bank's Fraud Policy, the five known patterns and the answer
rules, chunked from dataset/README.md. Chunks are retrieved by rule id or by keyword
overlap and handed to the investigator (as `document` evidence) and to the LLM (as grounding).
When TigerGraph is configured the same chunks live in the graph as PolicyDoc vertices
(see graph/load.py) and are fetched through clara.tg.policy_search.
"""
from __future__ import annotations

import functools
import os
import re

README = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dataset", "README.md")


@functools.lru_cache(maxsize=1)
def chunks() -> dict[str, dict]:
    if not os.path.exists(README):
        return {}
    text = open(README, encoding="utf-8").read()
    out = {}
    for m in re.finditer(r"\*\*(R\d+)\. ([^*]+)\*\*(.*?)(?=\n\*\*R\d+\.|\n### |\Z)", text, re.S):
        out[m.group(1)] = {"id": m.group(1), "section": f"Fraud Policy {m.group(1)}: {m.group(2).strip()}",
                           "text": " ".join((m.group(2) + m.group(3)).split())}
    for m in re.finditer(r"\*\*(\d)\. ([^*]+)\.\*\*(.*?)(?=\n\*\*\d\.|\n## |\Z)", text, re.S):
        key = f"P{m.group(1)}"
        out[key] = {"id": key, "section": f"Known pattern {m.group(1)}: {m.group(2).strip()}",
                    "text": " ".join(m.group(3).split())}
    for key, head in [("3a", "### 3a."), ("3b", "### 3b."), ("S2", "### 2. Approval routing"),
                      ("S4", "### 4. Exposure"), ("S5", "### 5. Gathering"), ("S6", "### 6. Stopping")]:
        i = text.find(head)
        if i >= 0:
            j = text.find("\n### ", i + 5)
            body = text[i:j if j > 0 else i + 2000]
            out[key] = {"id": key, "section": "Fraud Policy " + body.splitlines()[0].strip("# ").strip(),
                        "text": " ".join(body.split("\n", 1)[1].split())[:1200]}
    return out


def get(rule_id: str) -> dict | None:
    return chunks().get(rule_id)


def search(query: str, k: int = 3) -> list[dict]:
    q = set(re.findall(r"[a-z]{4,}", query.lower()))
    scored = []
    for c in chunks().values():
        words = set(re.findall(r"[a-z]{4,}", c["text"].lower()))
        s = len(q & words)
        if s:
            scored.append((s, c))
    return [c for _, c in sorted(scored, key=lambda x: -x[0])[:k]]
