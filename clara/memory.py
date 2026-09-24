"""
The agent's own case memory. Every finished investigation is stored with the entities it
touched (device profile, connected cards, affected transactions); the next investigation
retrieves any earlier case that shares one of those entities. Stored locally in
memory/agent_cases.jsonl and, when TigerGraph is configured, as InvestigationCase vertices
(clara.tg.write_case) so the graph itself carries the memory.
"""
from __future__ import annotations

import json
import os

PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "memory", "agent_cases.jsonl")


def _load() -> list[dict]:
    if not os.path.exists(PATH):
        return []
    with open(PATH) as f:
        return [json.loads(line) for line in f if line.strip()]


def reset():
    if os.path.exists(PATH):
        os.remove(PATH)


def related_cases(ctx, fired) -> list[dict]:
    """Earlier agent cases that share a device profile, customer or card with this one."""
    mine = {ctx["customer_id"], ctx["card_id"]}
    prof = ctx["flagged_profile"]
    if "shared_device" in fired:
        mine |= set(fired["shared_device"]["value"]["connected_cards"])
    out = []
    for m in _load():
        if m["case_id"] == ctx.get("case_id"):
            continue
        shared = []
        if prof and prof in m.get("device_profiles", []):
            shared.append(f"device profile '{prof[:40]}'")
        shared += sorted(mine & (set(m.get("cards", [])) | {m.get("customer_id"), m.get("card_id")}))
        if shared and m.get("verdict") in ("fraud", "uncertain"):
            out.append({**m, "shared": shared})
    return out


def remember(ans: dict, ctx: dict):
    rows = [m for m in _load() if m["case_id"] != ans["case_id"]]
    c = ans["case"]
    rows.append({
        "case_id": ans["case_id"], "customer_id": ctx["customer_id"], "card_id": ctx["card_id"],
        "verdict": c["verdict"], "pattern": c["pattern"], "fraud_probability": c["fraud_probability"],
        "device_profiles": c["connected_device_profiles"] + ([ctx["flagged_profile"]]
                                                            if c["verdict"] == "fraud" and ctx["flagged_profile"] else []),
        "cards": c["connected_card_ids"], "txns": c["affected_txn_ids"],
        "final_actions": [a["action"] for a in ans["next_best_actions"]["final"]],
        "opened_at": str(ctx["flagged"]["ts"]),
    })
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    with open(PATH, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
