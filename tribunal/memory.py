"""
Case memory for Tribunal.

The challenge requires the agent to "use case memory to improve investigations":
store findings/decisions/outcomes, retrieve similar prior cases, recognise recurring
entities across cases, and *update memory as new cases resolve*.

This is a file-backed store (JSONL under `cases/memory/`) that:
  * seeds from the bank's closed cases so recall works on the very first investigation;
  * is appended to as each new case resolves (so later cases in a run can retrieve
    earlier ones — memory visibly grows);
  * retrieves by a structural signature (cosine) blended with pattern match;
  * surfaces recurring entities (device profiles, connected cards) seen across fraud cases.

On a live instance this maps 1:1 to Case vertices + `sig_vec` native-vector retrieval
in TigerGraph (see graph/schema.gsql); the JSONL store is the offline mirror.
"""
from __future__ import annotations

import json
import math
import os
from functools import lru_cache

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MEM_DIR = os.path.join(ROOT, "cases", "memory")
MEM_FILE = os.path.join(MEM_DIR, "case_memory.jsonl")

_PATTERNS = ["card_testing", "card_not_present_fraud", "card_not_present_new_device",
             "out_of_region_use", "account_takeover", "undocumented", "none"]


def signature(pattern: str, exposure: float, n_evidence: int, shared: bool,
              prob: float) -> list[float]:
    """Structural signature used for case-memory retrieval (the offline mirror of the
    8-dim `sig_vec` upserted to TigerGraph)."""
    onehot = [1.0 if pattern == p else 0.0 for p in _PATTERNS]
    return onehot + [
        min(exposure / 2000.0, 1.0),
        min(n_evidence / 8.0, 1.0),
        1.0 if shared else 0.0,
        float(prob),
    ]


def _cos(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1e-9
    nb = math.sqrt(sum(x * x for x in b)) or 1e-9
    return dot / (na * nb)


class CaseMemory:
    def __init__(self, closed_records: list[dict] | None = None):
        os.makedirs(MEM_DIR, exist_ok=True)
        self.records: list[dict] = []
        self._ids: set[str] = set()
        if closed_records:
            for r in closed_records:
                self._add(self._from_closed(r), persist=False)
        self._load()

    # -- construction -------------------------------------------------------------
    def _from_closed(self, r: dict) -> dict:
        pat = r.get("pattern", "none")
        exp = float(r.get("exposure_usd", 0) or 0)
        shared = bool(r.get("connected_card_ids"))
        prob = 0.9 if r.get("outcome") == "confirmed_fraud" else 0.05
        return {
            "case_id": r.get("case_id"), "kind": "closed",
            "pattern": pat, "outcome": r.get("outcome"),
            "exposure_usd": exp, "connected_card_ids": r.get("connected_card_ids", []),
            "device_profiles": [], "customer_id": r.get("customer_id"),
            "sig": signature(pat, exp, r.get("n_txns", 0) or 0, shared, prob),
            "note": (r.get("notes") or "")[:240],
        }

    def _load(self):
        if not os.path.exists(MEM_FILE):
            return
        with open(MEM_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    self._add(json.loads(line), persist=False)
                except json.JSONDecodeError:
                    continue

    def _add(self, rec: dict, persist: bool):
        cid = rec.get("case_id")
        if cid in self._ids:
            # newest wins (a re-resolved case updates its record)
            self.records = [r for r in self.records if r.get("case_id") != cid]
        self._ids.add(cid)
        self.records.append(rec)
        if persist:
            with open(MEM_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, default=str) + "\n")

    # -- the two operations the agent uses ---------------------------------------
    def recall(self, sig: list[float], pattern: str, k: int = 3,
               exclude: str | None = None) -> list[dict]:
        """Retrieve the most similar prior cases (cosine on signature, +0.1 pattern match)."""
        scored = []
        for r in self.records:
            if exclude and r.get("case_id") == exclude:
                continue
            s = _cos(sig, r["sig"]) + (0.1 if r.get("pattern") == pattern else 0.0)
            scored.append((s, r))
        scored.sort(key=lambda x: x[0], reverse=True)
        out = []
        for s, r in scored[:k]:
            out.append({"case_id": r.get("case_id"), "pattern": r.get("pattern"),
                        "outcome": r.get("outcome"), "exposure_usd": r.get("exposure_usd", 0),
                        "similarity": round(float(min(s, 1.0)), 3),
                        "kind": r.get("kind", "closed"), "note": r.get("note", "")})
        return out

    def remember(self, record: dict):
        """Write a resolved case back to memory (the graph write-back, offline mirror)."""
        self._add(record, persist=True)

    def recurring_entities(self) -> dict:
        """Entities (device profiles, connected cards) recurring across fraud cases —
        the cross-case relationships the challenge asks the agent to recognise."""
        dev, card = {}, {}
        for r in self.records:
            if r.get("outcome") not in ("confirmed_fraud", "fraud"):
                continue
            for p in r.get("device_profiles", []) or []:
                if p:
                    dev[p] = dev.get(p, 0) + 1
            for c in r.get("connected_card_ids", []) or []:
                if c:
                    card[c] = card.get(c, 0) + 1
        return {
            "device_profiles": sorted([{"profile": p, "cases": n} for p, n in dev.items()
                                       if n > 1], key=lambda x: -x["cases"])[:10],
            "connected_cards": sorted([{"card": c, "cases": n} for c, n in card.items()
                                       if n > 1], key=lambda x: -x["cases"])[:10],
        }

    def size(self) -> int:
        return len(self.records)


@lru_cache(maxsize=1)
def get_memory() -> CaseMemory:
    """Process-global memory, seeded from the closed cases in the dataset."""
    from .realdata import load
    try:
        closed = load().memory
    except Exception:
        closed = []
    return CaseMemory(closed_records=closed)
