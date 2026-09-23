# 2. How we built it — part by part (easy language)

Think of the whole thing as a small factory. A fraud alert goes in one end, and a complete
"court verdict" comes out the other end. Let's walk through the machine.

## Step 0 — the data we have

The dataset (folder `dataset/`) has 4 files:

| File | In plain words |
|---|---|
| `transactions.csv` | Every card payment (590,000 of them). Amount, time, card, device, region, and the bank's **risk score**. |
| `identity.csv` | Extra details for online payments: what **device** was used, the browser, the screen size, whether it was a **new device**, whether a proxy was used. |
| `closed_cases_history.csv` | 5,565 **old cases the bank already solved** — we know which were real fraud and which were false alarms. This is our **memory**. |
| `case_pack.csv` | The **20 exam cases** we must investigate and answer. |

One tricky fact we discovered: a payment tells us the **customer**, but not exactly which
"card number." So we investigate at the **customer level** — we look at everything that
customer did.

## Step 1 — make the data fast (`data/build_cache.py`)

The transactions file is huge (675 MB). Reading it every time would be slow. So we run one
script **once** that keeps only the columns we need and saves a small, fast file
(`_slim.parquet`). After that, everything loads in about 1 second.

*(Think: taking a giant messy warehouse and putting just the useful items on one neat shelf.)*

## Step 2 — build the "graph" (`clara/realdata.py`)

A **graph** is just **dots joined by lines**:
- Dots = customers, cards, **devices**, **regions (locations)**, email domains.
- Lines = "this payment used this device", "this card billed in this region", etc.

Why bother? Because **fraud hides in the connections**. One stolen device used by 27
different cards is invisible if you look at payments one by one — but on a graph it lights
up like a spider web. This file builds those connections and also loads our **memory** (the
old solved cases).

It also does one smart thing: it tells the difference between a **generic device**
("Windows + Chrome" — used by thousands of normal people, means nothing) and a **specific
device** ("Samsung SM-G935F build number…" — a real physical phone; if 27 cards use it,
that's a fraud ring). We only treat *specific* devices as a real link. This one rule stops
tons of false alarms.

## Step 3 — the two investigators (`clara/signals.py`)

This file is a **toolbox of small questions we can ask the graph**. Each question is called
a **probe**. Each probe returns a piece of evidence *and* a number saying how much it moves
our suspicion up or down (we call that number a **likelihood ratio** — think "how strongly
does this point to fraud?").

**Prosecutor probes** (look for fraud):
- *Shared device* — how many other cards used this exact device? (a ring)
- *Link to known fraud* — is this device/customer connected to an old confirmed fraud?
- *Card testing* — lots of tiny payments in an hour, then a big one? (testing a stolen card)
- *Burst* — several online payments crammed into 48 hours.
- *New device / proxy* — payment from a device never seen before, hiding behind a proxy.
- *Out of region* — a shop payment in a city the customer never visits, *while* they're
  also active at home (the card can't be in two places → it's a clone).

**Defender probes** (look for an innocent explanation):
- *Familiar device* — this device has been used by this customer for months. Not new.
- *Familiar merchant* — the customer has paid this shop many times before.
- *Consistent amount* — the amount is normal for this customer.
- *Recurring charge* — it's a subscription they forgot about (Netflix, gym…).
- *Clean history* — long, calm history, one device, never any fraud.

So the "Prosecutor vs Defender" is really just **two lists of graph questions**: one hunting
for guilt, one hunting for innocence. No fancy chatbots arguing — just real evidence from
the data. (This is important: it means the demo can't glitch or make things up.)

## Step 4 — the confidence meter (`clara/belief.py`)

We keep a single number: **"how likely is this fraud, right now?"** — like a dial from 0% to 100%.

- It starts at a low, sensible guess (25%).
- Every prosecutor probe that fires pushes the dial **up**.
- Every defender probe that fires pushes the dial **down**.

We use a well-known, honest math for this (log-odds / Bayesian updating), so the dial always
moves in a fair, explainable way. You can literally see *why* it went from 61% to 93%.

## Step 5 — the Judge and the rulebook (`clara/policy_real.py` + `clara/investigator.py`)

The **Judge** does three things:

1. **Reads the meter.** Very high → lean fraud. Very low → lean normal. In the middle → unsure.
2. **Applies your rule:** *only act if fraud survives its strongest defense.* If the Defender
   has one strong, specific innocent explanation (recurring charge, months-old device) and the
   Prosecutor has no hard proof (no ring, no known-fraud link), we **don't block**.
3. **Follows the bank's rulebook (the Fraud Policy, rules R1–R10).** The policy is a fixed list
   the challenge gave us. It says exactly which **action** to take and **who must approve it**:

| Action | Who approves |
|---|---|
| Allow, monitor, warn, verify, step-up, create case | **auto** (agent can do it) |
| Decline a payment; block a card under $2,500 | **L1** (team lead) |
| Block a card over $2,500; block all cards; file a report | **L2** (fraud manager) |

The Judge is **deterministic** — same evidence always gives the same decision. That's on
purpose: banks need decisions they can audit and trust, not a random AI guess.

## Step 6 — "I'm not sure, get me more evidence"

This is the heart of the challenge. If the meter is stuck in the middle (say 55%), the Judge
does **not** guess. It says: *"the missing piece is the customer's word"* → recommends
**STEP-UP AUTHENTICATION** or **VERIFY WITH CUSTOMER**.

We then **simulate** the customer's answer (the challenge says to pretend the reply and write
down what we assumed):
- If our graph evidence leaned fraud → we assume the customer says *"I didn't do it"* → meter
  jumps up → **BLOCK**.
- If it leaned normal → customer says *"yes that was me"* → meter drops → **ALLOW**.

So the recommendation **changes** as evidence arrives — we record both the **initial** action
and the **final** action, and what changed. (Judges love this.)

## Step 7 — what comes out (`run_cases.py` → `cases/HHG-XXX.json`)

For each of the 20 cases we write one answer file with **three parts** (the exact format the
challenge demands):

1. **The case** — verdict (fraud / legitimate / uncertain), confidence %, the fraud pattern,
   the affected payments, the connected cards, the money at risk, the **evidence list**
   (with the two sides), and the **similar old cases** we used as memory.
2. **The report (SAR)** — a formal fraud report, but only when the rules require one.
3. **The next best action** — what to do, and who approves — **before** and **after** asking
   the customer.

Plus the **debate** block (prosecution points, defense points, the Judge's conclusion) that
powers the courtroom screen.

## The file map (who does what)

```
data/build_cache.py     → shrink the huge file so it loads fast
clara/realdata.py       → build the graph (dots + lines) + load memory of old cases
clara/signals.py        → the Prosecutor & Defender questions (probes)
clara/belief.py         → the confidence meter (the math)
clara/policy_real.py    → the bank's rulebook (actions, approvals, when to file a report)
clara/investigator.py   → the Judge: run both sides, weigh, decide, ask for more if unsure
clara/narrate_real.py   → write the human summary + the formal fraud report
run_cases.py            → do all 20 cases → answer files
app/streamlit_app.py    → the screen you demo (belief meter, evidence, verdict)
graph/                  → the TigerGraph version (see doc 3)
```

---

👉 Next: **[03_TIGERGRAPH_MCP_GRAPHRAG.md](03_TIGERGRAPH_MCP_GRAPHRAG.md)** — the three required
technologies (TigerGraph, MCP, GraphRAG), explained simply, and exactly how we use each.
