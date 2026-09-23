"""
Generate a schema-accurate synthetic sample of the IEEE-CIS (HHGOA_IEEE) dataset.

Why: the real dataset isn't wired in yet. This produces the SAME columns so CLARA
runs end-to-end today; drop the real train_transaction.csv / train_identity.csv into
data/raw/ and point the loader there to switch over -- no code changes.

We deliberately PLANT fraud so the agent has something real to find:
  - Ring A  : device-sharing account farm (one DeviceInfo across many card1s)
  - Ring B  : shared-email ring (one P_emaildomain across many card1s)
  - Burst C : card-testing velocity burst (one card1, many tiny txns in a short window)
  - Case D  : lone identity-mismatch (M-flags F + distance anomaly)
  - plus benign background traffic

Outputs (to data/):
  train_transaction.csv   train_identity.csv
  cases.csv               (closed historical cases = memory seeds, with outcomes)
  benchmark.csv           (trigger transactions the agent must investigate)
  policy.md  patterns.md  (GraphRAG grounding docs)
"""
import csv
import math
import os
import random

random.seed(42)  # deterministic

HERE = os.path.dirname(os.path.abspath(__file__))
DAY = 24 * 3600

# background pool is all COMMON domains; "anonymous.com" is reserved for the email ring
EMAIL_DOMAINS = ["gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "aol.com"]
# Background devices are all GENERIC (hub) strings. The distinctive fraud-ring devices
# ("SAMSUNG SM-G960F", "Trident/7.0") are used ONLY by planted rings, so they stay
# high-cardinality-but-small and form real, detectable clusters.
DEVICES = ["Windows", "iOS Device", "MacOS", "rv:11.0"]
PRODUCTCD = ["W", "C", "R", "H", "S"]
CARD4 = ["visa", "mastercard", "amex", "discover"]
CARD6 = ["debit", "credit"]

rows_txn = []
rows_id = []
_next_id = [3000000]


def new_txn_id():
    _next_id[0] += 1
    return _next_id[0]


def base_risk():
    # bank model risk score in [0,1], background skewed low
    return round(min(0.99, abs(random.gauss(0.15, 0.12))), 3)


def add_txn(card1, dt, amt, risk, product="W", addr1=204, p_email="gmail.com",
            r_email="", device=None, m_mismatch=False, dist1=10):
    tid = new_txn_id()
    m_vals = {}
    for i in range(1, 10):
        if m_mismatch and i in (2, 3, 4, 5, 6):
            m_vals[f"M{i}"] = "F"
        else:
            # benign default: mostly match ("T"), only ~12% incidental "F" (low noise)
            m_vals[f"M{i}"] = "F" if random.random() < 0.12 else "T"
    txn = {
        "TransactionID": tid,
        "TransactionDT": int(dt),
        "TransactionAmt": round(amt, 2),
        "ProductCD": product,
        "card1": card1,
        "card2": 100 + (card1 % 500),
        "card3": 150,
        "card4": random.choice(CARD4),
        "card5": 200 + (card1 % 100),
        "card6": random.choice(CARD6),
        "addr1": addr1,
        "addr2": 87,
        "dist1": dist1,
        "dist2": "",
        "P_emaildomain": p_email,
        "R_emaildomain": r_email,
        "risk_score": risk,
    }
    # C1-C14 counting features (associations per card); D1-D15 timedeltas
    for i in range(1, 15):
        txn[f"C{i}"] = random.randint(1, 4)
    for i in range(1, 16):
        txn[f"D{i}"] = random.choice([0, 1, 2, 7, 14, 30, ""])
    txn["D1"] = int(dt / DAY) - random.randint(0, 60)  # days since card first seen (for UID)
    txn.update(m_vals)
    rows_txn.append(txn)

    # ~24% of txns have an identity row; planted-fraud txns always do (device matters)
    if device is not None or random.random() < 0.24:
        dev = device or random.choice(DEVICES)
        rows_id.append({
            "TransactionID": tid,
            "id_01": random.randint(-50, 0),
            "id_02": random.randint(1000, 90000),
            "id_05": random.randint(0, 30),
            "id_06": random.randint(-30, 0),
            "id_11": round(random.uniform(90, 100), 1),
            "id_12": random.choice(["Found", "NotFound"]),
            "id_15": random.choice(["New", "Found", "Unknown"]),
            "id_30": random.choice(["Windows 10", "iOS 11.1.2", "Mac OS X 10_13", "Android 7.0"]),
            "id_31": random.choice(["chrome 62.0", "mobile safari 11.0", "ie 11.0 for desktop", "firefox 57.0"]),
            "id_33": random.choice(["1920x1080", "1334x750", "2208x1242", "1280x800"]),
            "DeviceType": "mobile" if "SM-" in dev or "iOS" in dev else "desktop",
            "DeviceInfo": dev,
        })
    return tid


# ---- benign background -------------------------------------------------------
for _ in range(600):
    card1 = random.randint(1000, 18000)
    dt = random.randint(1, 120) * DAY + random.randint(0, DAY)
    add_txn(card1, dt, random.uniform(20, 400), base_risk(),
            product=random.choice(PRODUCTCD),
            addr1=random.choice([204, 299, 330, 191]),
            p_email=random.choice(EMAIL_DOMAINS))

# ---- Ring A: device-sharing account farm ------------------------------------
RING_A_DEVICE = "SAMSUNG SM-G960F"
ring_a_cards = list(range(50001, 50009))
ring_a_txns = []
for c in ring_a_cards:
    for _ in range(random.randint(2, 4)):
        dt = random.randint(95, 120) * DAY + random.randint(0, DAY)
        # priors kept < 0.70 so the trigger alone isn't decisive -> agent must investigate.
        # Ring A's signal is the shared DEVICE only (common email), so it stays a clean
        # account_farming pattern distinct from the email ring.
        tid = add_txn(c, dt, random.uniform(300, 900), round(random.uniform(0.45, 0.66), 3),
                      addr1=204, p_email="gmail.com", device=RING_A_DEVICE)
        ring_a_txns.append(tid)

# ---- Ring B: shared-email ring ----------------------------------------------
RING_B_EMAIL = "anonymous.com"
ring_b_cards = list(range(60001, 60006))
for c in ring_b_cards:
    for _ in range(random.randint(2, 3)):
        dt = random.randint(90, 120) * DAY + random.randint(0, DAY)
        add_txn(c, dt, random.uniform(150, 500), round(random.uniform(0.4, 0.6), 3),
                addr1=299, p_email=RING_B_EMAIL, device=random.choice(DEVICES))

# ---- Burst C: card-testing velocity -----------------------------------------
# 70001 = closed-case seed; 70002 = fresh benchmark subject (same pattern)
for burst_card, start_day in [(70001, 110), (70002, 114)]:
    burst_start = start_day * DAY
    for i in range(16):
        add_txn(burst_card, burst_start + i * 600, random.uniform(1.0, 9.0),  # tiny amounts, 10-min gaps
                round(random.uniform(0.35, 0.55), 3), addr1=330, p_email="gmail.com",
                device="Windows")

# ---- Case D: lone identity mismatch -----------------------------------------
# 80001 = closed-case seed; 80002 = fresh benchmark subject
for mismatch_card, dist in [(80001, 870), (80002, 910)]:
    add_txn(mismatch_card, 108 * DAY, 1250.0, 0.58, addr1=191, p_email="hotmail.com",
            device="Trident/7.0", m_mismatch=True, dist1=dist)

# ---- Benign benchmark subject (should CLEAR despite a high risk-score trigger)
# multiple clean txns so incidental M-flag noise averages out
for k in range(3):
    add_txn(15000, (100 + k * 4) * DAY, random.uniform(180, 300), 0.55,
            addr1=204, p_email="gmail.com", device="MacOS")

# =============================================================================
# Closed historical cases (memory seeds) -- outcomes known
# =============================================================================
cases = [
    # confirmed device-farm cases (teach: shared-device + anon email -> fraud)
    {"case_id": "C-1001", "card1": 50001, "typology": "account_farming",
     "outcome": "confirmed_fraud", "action_taken": "block_card",
     "note": "8 cards on one device, anonymous email; ring confirmed."},
    {"case_id": "C-1002", "card1": 50002, "typology": "account_farming",
     "outcome": "confirmed_fraud", "action_taken": "freeze_account",
     "note": "Shared device SM-G960F; step-up failed."},
    {"case_id": "C-1003", "card1": 60001, "typology": "shared_email_ring",
     "outcome": "confirmed_fraud", "action_taken": "block_card",
     "note": "Multiple cards under anonymous.com."},
    # card testing
    {"case_id": "C-1004", "card1": 70001, "typology": "card_testing",
     "outcome": "confirmed_fraud", "action_taken": "block_card",
     "note": "Micro-amount burst, 16 txns in 3h."},
    # cleared cases (teach: high risk score alone, but customer confirmed -> clear)
    {"case_id": "C-1005", "card1": 12000, "typology": "none",
     "outcome": "cleared", "action_taken": "allow",
     "note": "High risk score but customer validated purchase; no ring."},
    {"case_id": "C-1006", "card1": 13500, "typology": "none",
     "outcome": "cleared", "action_taken": "allow",
     "note": "Travel purchase, distance anomaly explained; step-up passed."},
    {"case_id": "C-1007", "card1": 14200, "typology": "none",
     "outcome": "cleared", "action_taken": "allow",
     "note": "Single device, long-tenured card, no shared attributes."},
    {"case_id": "C-1008", "card1": 80001, "typology": "identity_mismatch",
     "outcome": "confirmed_fraud", "action_taken": "escalate",
     "note": "Name/address mismatch + 870mi distance; ATO confirmed."},
]

# =============================================================================
# Benchmark triggers (what the agent must investigate). Mix of clear/uncertain.
# =============================================================================
benchmark = [
    {"bench_id": "B-01", "card1": 50005, "reason": "risk_score>0.5", "hint": "device farm (Ring A)"},
    {"bench_id": "B-02", "card1": 60004, "reason": "risk_score>0.5", "hint": "email ring (Ring B)"},
    {"bench_id": "B-03", "card1": 70002, "reason": "velocity_alert", "hint": "card testing (Burst C)"},
    {"bench_id": "B-04", "card1": 80002, "reason": "analyst_request", "hint": "identity mismatch (Case D)"},
    {"bench_id": "B-05", "card1": 15000, "reason": "risk_score>0.5", "hint": "benign / should clear"},
]


def write_csv(name, rows, fieldnames):
    path = os.path.join(HERE, name)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fieldnames})
    print(f"  wrote {name:24s} {len(rows):5d} rows")


txn_fields = (["TransactionID", "TransactionDT", "TransactionAmt", "ProductCD",
               "card1", "card2", "card3", "card4", "card5", "card6",
               "addr1", "addr2", "dist1", "dist2", "P_emaildomain", "R_emaildomain",
               "risk_score"]
              + [f"C{i}" for i in range(1, 15)]
              + [f"D{i}" for i in range(1, 16)]
              + [f"M{i}" for i in range(1, 10)])
id_fields = ["TransactionID", "id_01", "id_02", "id_05", "id_06", "id_11",
             "id_12", "id_15", "id_30", "id_31", "id_33", "DeviceType", "DeviceInfo"]

print("Generating synthetic HHGOA_IEEE sample...")
write_csv("train_transaction.csv", rows_txn, txn_fields)
write_csv("train_identity.csv", rows_id, id_fields)
write_csv("cases.csv", cases,
          ["case_id", "card1", "typology", "outcome", "action_taken", "note"])
write_csv("benchmark.csv", benchmark, ["bench_id", "card1", "reason", "hint"])

POLICY_MD = """# Fraud Action Policy (synthetic, stands in for HHGOA_IEEE policy)

## Confidence thresholds
- **Block / freeze** requires fraud confidence >= 0.85 OR (confidence >= 0.70 AND exposure >= $1000).
- **Clear / allow** when confidence <= 0.20.
- Between 0.20 and the block threshold: gather more evidence, or step-up / monitor.

## Approval routes
- **Auto**: warn customer, add to watchlist, request step-up auth.
- **L1 analyst**: monitor account, request customer validation.
- **L2 analyst (human)**: block card, freeze account, file SAR.

## Suspicious Activity Report (SAR)
- File a SAR when confidence >= 0.85 AND typology in {account_farming, shared_email_ring,
  card_testing, identity_mismatch}. SAR must record the 5W1H and cite graph evidence.

## Evidence friction
- Prefer low-friction graph evidence. Only request customer contact / step-up when it can
  change the decision (value-of-information positive) and cheaper evidence is exhausted.
"""
with open(os.path.join(HERE, "policy.md"), "w") as f:
    f.write(POLICY_MD)

with open(os.path.join(HERE, "patterns.md"), "w") as f:
    f.write("""# Known Fraud Typologies (synthetic)

1. **account_farming** - many distinct cards (card1) transacting from ONE device
   (DeviceInfo + id_33 fingerprint), often with anonymous email. Graph signal:
   high shared-device degree.
2. **shared_email_ring** - many cards under one P_emaildomain. Graph signal: high
   shared-email degree.
3. **card_testing** - one card, burst of tiny-amount transactions in a short
   TransactionDT window (validating stolen card numbers). Signal: velocity spike.
4. **identity_mismatch / ATO** - M1-M9 match flags = F plus dist1/dist2 anomaly;
   new device on an existing card. Signal: mismatch + distance.
5. **(undocumented)** - not every pattern is documented; dense clusters matching
   none of the above are candidate new typologies.
""")

print("Done. Files in", HERE)
