"""
The HHGOA Fraud Policy, encoded. Exact action identifiers + approval routes + the
thresholds from rules R1-R10 and sections 3a/4/6. Decisions are deterministic here;
the investigator assembles ordered action lists and cites the rule numbers.
"""
from __future__ import annotations

# thresholds
CREATE_CASE_PROB = 0.30       # 3a: open a case at/above this probability
VERIFY_BELOW = 0.70           # R1: below this on a single signal, verify before blocking
STOP_HIGH = 0.85              # section 6
STOP_LOW = 0.15
SAR_EXPOSURE = 1000.0         # 3a
BLOCK_L2_EXPOSURE = 2500.0    # section 2
ESCALATE_EXPOSURE = 500.0     # R4/R8

AUTO = {"ALLOW_TRANSACTION", "MONITOR_CARD", "MONITOR_CONNECTED_CARDS", "WARN_CUSTOMER",
        "VERIFY_WITH_CUSTOMER", "STEP_UP_AUTH", "GENERATE_REPORT", "CREATE_CASE",
        "ESCALATE_TO_ANALYST", "CLOSE_NO_FRAUD"}


def route_for(action: str, exposure: float) -> str:
    if action == "BLOCK_CARD":
        return "L2" if exposure > BLOCK_L2_EXPOSURE else "L1"
    if action == "DECLINE_TRANSACTION":
        return "L1"
    if action in ("BLOCK_ALL_CARDS", "FILE_REPORT"):
        return "L2"
    return "auto"


def should_file_report(verdict: str, prob: float, exposure: float,
                       shared_origin: bool, pattern: str) -> bool:
    """3a: confirmed/strongly suspected AND (exposure>$1000 OR shared origin OR undocumented)."""
    strong = verdict == "fraud" or prob >= STOP_HIGH
    criterion = exposure > SAR_EXPOSURE or shared_origin or pattern == "undocumented"
    return strong and criterion


def act(action: str, exposure: float, reason: str) -> dict:
    return {"action": action, "route": route_for(action, exposure), "reason": reason}
