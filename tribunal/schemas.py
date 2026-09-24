"""Data models for a Tribunal investigation."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class EvidenceResult:
    """One piece of evidence the agent gathered."""
    key: str
    label: str
    raw_value: Any            # the observed value (count, hops, response, ...)
    bucket: str               # discretized outcome bucket
    likelihood_ratio: float   # LR applied to the belief for this bucket
    cost: float               # friction cost paid to obtain it
    prob_before: float        # fraud probability before this evidence
    prob_after: float         # fraud probability after this evidence
    rationale: str = ""       # why this evidence was chosen (VoI)

    @property
    def delta(self) -> float:
        return self.prob_after - self.prob_before


@dataclass
class Action:
    name: str                 # allow | monitor | step_up | block_card | freeze_account | escalate | file_sar
    approval_route: str       # auto | L1 | L2
    reason: str


@dataclass
class Case:
    case_id: str
    trigger: str              # what started the investigation
    card1: int
    prior_prob: float         # from bank risk score
    evidence: list[EvidenceResult] = field(default_factory=list)
    final_prob: float = 0.0
    typology: str = "unknown"
    exposure: float = 0.0
    stop_reason: str = ""
    # NBA is recorded twice per the challenge: before extra evidence and after.
    nba_initial: Optional[Action] = None
    nba_final: Optional[Action] = None
    actions: list[Action] = field(default_factory=list)
    sar: Optional[dict] = None
    narrative: str = ""
    similar_cases: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "trigger": self.trigger,
            "card1": self.card1,
            "prior_prob": round(self.prior_prob, 4),
            "final_prob": round(self.final_prob, 4),
            "typology": self.typology,
            "exposure": round(self.exposure, 2),
            "stop_reason": self.stop_reason,
            "nba_initial": vars(self.nba_initial) if self.nba_initial else None,
            "nba_final": vars(self.nba_final) if self.nba_final else None,
            "actions": [vars(a) for a in self.actions],
            "evidence_timeline": [
                {
                    "step": i + 1,
                    "key": e.key,
                    "label": e.label,
                    "chosen_because": e.rationale,
                    "observed": e.raw_value,
                    "bucket": e.bucket,
                    "likelihood_ratio": e.likelihood_ratio,
                    "cost": e.cost,
                    "prob_before": round(e.prob_before, 4),
                    "prob_after": round(e.prob_after, 4),
                }
                for i, e in enumerate(self.evidence)
            ],
            "similar_cases": self.similar_cases,
            "sar": self.sar,
            "narrative": self.narrative,
        }
