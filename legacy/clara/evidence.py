"""
The evidence catalog: everything CLARA can look at, with a cost and a calibrated
likelihood ratio per outcome bucket.

Each Evidence declares:
  fetch(client, card1) -> raw value          how to obtain it (graph query or action)
  bucketize(raw)       -> bucket label        discretize the observation
  LR[bucket]           -> likelihood ratio     how much that outcome moves the belief
  priors               -> [(p, bucket), ...]   outcome distribution used for VoI planning
  cost                 -> friction units       cheap graph query vs expensive customer contact

The LRs are the ONE thing to calibrate against real closed cases. The values below
are hand-tuned so the synthetic demo tells a coherent story.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


@dataclass
class Evidence:
    key: str
    label: str
    cost: float
    fetch: Callable
    bucketize: Callable
    LR: dict
    priors: list                      # [(prob, bucket)]
    typology_hint: str = ""           # typology this evidence supports when positive
    is_action: bool = False           # controlled action (needs it be worth the friction)

    def lr_for(self, bucket: str) -> float:
        return self.LR.get(bucket, 1.0)


def _band(value, thresholds, labels):
    for t, lab in zip(thresholds, labels):
        if value <= t:
            return lab
    return labels[-1]


CATALOG: list[Evidence] = [
    Evidence(
        key="shared_device",
        label="Shared-device peer count",
        cost=1.0,
        fetch=lambda c, x: c.shared_device_count(x),
        bucketize=lambda v: _band(v, [1, 4], ["none", "some", "many"]),
        LR={"none": 0.6, "some": 3.0, "many": 8.0},
        priors=[(0.6, "none"), (0.25, "some"), (0.15, "many")],
        typology_hint="account_farming",
    ),
    Evidence(
        key="path_to_fraud",
        label="Hops to a known-fraud entity",
        cost=1.2,
        fetch=lambda c, x: c.hops_to_known_fraud(x),
        bucketize=lambda v: ("h0" if v == 0 else "h1" if v == 1 else "h2" if v == 2
                             else "h3" if v == 3 else "far"),
        LR={"h0": 15.0, "h1": 12.0, "h2": 5.0, "h3": 2.0, "far": 0.5},
        priors=[(0.55, "far"), (0.15, "h3"), (0.15, "h2"), (0.1, "h1"), (0.05, "h0")],
        typology_hint="account_farming",
    ),
    Evidence(
        key="velocity",
        label="Max transactions in 24h (velocity)",
        cost=1.0,
        fetch=lambda c, x: c.velocity_24h(x),
        bucketize=lambda v: _band(v, [4, 9], ["low", "med", "high"]),
        LR={"low": 0.8, "med": 2.5, "high": 6.0},
        priors=[(0.7, "low"), (0.2, "med"), (0.1, "high")],
        typology_hint="card_testing",
    ),
    Evidence(
        key="identity_mismatch",
        label="Identity-match / distance anomaly",
        cost=1.0,
        fetch=lambda c, x: c.identity_mismatch_score(x),
        bucketize=lambda v: _band(v, [0.2, 0.5], ["none", "some", "strong"]),
        LR={"none": 0.7, "some": 1.8, "strong": 4.0},
        priors=[(0.6, "none"), (0.28, "some"), (0.12, "strong")],
        typology_hint="identity_mismatch",
    ),
    Evidence(
        key="shared_email",
        label="Shared rare-email peer count",
        cost=1.0,
        fetch=lambda c, x: c.shared_email_count(x),
        bucketize=lambda v: _band(v, [1, 4], ["none", "some", "many"]),
        LR={"none": 0.9, "some": 1.5, "many": 3.0},
        priors=[(0.75, "none"), (0.15, "some"), (0.1, "many")],
        typology_hint="shared_email_ring",
    ),
    Evidence(
        key="similar_cases",
        label="Outcome of most-similar past cases",
        cost=1.5,
        fetch=lambda c, x: c.similar_cases(x, k=3),
        bucketize=lambda res: _similar_bucket(res),
        LR={"mostly_fraud": 6.0, "mixed": 1.0, "mostly_cleared": 0.4},
        priors=[(0.4, "mostly_fraud"), (0.3, "mixed"), (0.3, "mostly_cleared")],
    ),
    # -------- controlled actions (expensive; only fire when VoI justifies) -----
    Evidence(
        key="ask_customer",
        label="Ask account owner to validate",
        cost=10.0,
        is_action=True,
        fetch=lambda c, x: c.ask_customer(x),
        bucketize=lambda r: r,  # "unauthorized" | "confirmed"
        LR={"unauthorized": 20.0, "confirmed": 0.05},
        priors=[(0.5, "unauthorized"), (0.5, "confirmed")],
    ),
    Evidence(
        key="step_up_auth",
        label="Request step-up authentication",
        cost=8.0,
        is_action=True,
        fetch=lambda c, x: c.step_up_auth(x),
        bucketize=lambda r: r,  # "failed" | "passed"
        LR={"failed": 15.0, "passed": 0.15},
        priors=[(0.5, "failed"), (0.5, "passed")],
    ),
]


def _similar_bucket(res: list) -> str:
    if not res:
        return "mixed"
    # weight by similarity
    fraud = sum(r["similarity"] for r in res if r["outcome"] == "confirmed_fraud")
    clear = sum(r["similarity"] for r in res if r["outcome"] == "cleared")
    if fraud > clear * 1.3:
        return "mostly_fraud"
    if clear > fraud * 1.3:
        return "mostly_cleared"
    return "mixed"


def by_key(key: str) -> Evidence:
    for e in CATALOG:
        if e.key == key:
            return e
    raise KeyError(key)
