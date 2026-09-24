"""
Belief state as log-odds. Prior = bank risk score; each evidence adds ln(LR).

This is the mathematical heart of DefAttack: fraud belief is a probability that moves
by Bayesian log-odds updates, so every step is auditable ("we went from 0.61 to 0.88
because the shared-device check had likelihood ratio 8.0").
"""
from __future__ import annotations

import math

_CLAMP = 12.0  # keep log-odds finite (~1e-5 .. 0.99999)


def prob_to_logodds(p: float) -> float:
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def logodds_to_prob(lo: float) -> float:
    lo = max(min(lo, _CLAMP), -_CLAMP)
    return 1.0 / (1.0 + math.exp(-lo))


class Belief:
    def __init__(self, prior_prob: float):
        self.logodds = prob_to_logodds(prior_prob)

    @property
    def prob(self) -> float:
        return logodds_to_prob(self.logodds)

    def update(self, likelihood_ratio: float) -> float:
        """Apply one evidence LR. Returns the new probability."""
        lr = max(likelihood_ratio, 1e-6)
        self.logodds = max(min(self.logodds + math.log(lr), _CLAMP), -_CLAMP)
        return self.prob
