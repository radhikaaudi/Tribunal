# Tribunal — Confidence-Led Adaptive Risk Agent

> An agentic fraud investigator for the **TigerGraph Agentic Fraud Investigation — Hacker House Goa 2026** challenge, running on the real HHGOA / IEEE-CIS dataset.

Tribunal takes a case from the case pack, investigates it against the transaction graph and the bank's closed cases, works out **what kind of fraud it is (if any)**, **how far it goes**, and **what to do next under the Fraud Policy** — and it **knows when to stop**. It treats an investigation as an explicit uncertainty-reduction loop: a fraud belief (log-odds) starts from a base rate nudged by the risk score / customer report — *never* the risk score as a verdict — and moves by auditable Bayesian updates as evidence arrives. It stops the moment the decision is settled (policy §6), then recommends a policy-bound next-best-action with the correct approval route, and files a SAR only when policy 3a calls for one.

Output: **one `cases/HHG-XXX.json` per case, in the exact submission format** (case + evidence_requests + next_best_actions.initial/final + sar + stop_reason).

---

## Quickstart

```bash
cd tribunal
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python data/build_cache.py     # one-time: dataset/*.csv (675MB) -> slim parquet (~15s)
python run_cases.py            # investigate all 20 cases -> cases/HHG-XXX.json
streamlit run app/streamlit_app.py   # analyst console (belief meter, evidence, NBA, SAR)
```

`.env` is optional (works offline with template SAR narratives). Add an LLM key to
polish narratives, or TigerGraph creds to run against a live instance.

---

## How it works

```
 case (risk_score | customer_report | analyst_request)
        │
        ▼  base-rate belief, nudged by the trigger (score is a reason to look, not a verdict)
   ┌─────────────┐   run evidence probes over the graph      ┌────────────────────────┐
   │ investigator│ ────────────────────────────────────────▶ │ signals.py probes:      │
   │  (belief +  │   each fires -> belief.update(LR)          │ card-testing, CNP burst,│
   │   policy)   │ ◀──────────────────────────────────────── │ new device, out-of-     │
   └──────┬──────┘   stop at 0.85/0.15 w/ >=2 evidence (§6)   │ region, shared-device   │
          │                                                   │ ring, link-to-known-    │
          │  R1: verify before block on a weak signal         │ fraud, recurring-match  │
          │  simulate customer/step-up response (stubbed)     └────────────┬────────────┘
          ▼                                                                │
   policy_real.py (R1-R10, routes, SAR 3a)                       realdata.py  ──▶ TigerGraph
   narrate_real.py (SAR 5W1H narrative)                          (pandas graph | GSQL+vectors+MCP)
          │
          ▼
   case + initial/final NBA + SAR + similar prior cases + case written to graph
```

**Design rule:** the LLM only narrates; every decision (verdict thresholds, actions,
routes, SAR) is a deterministic function of the evidence — auditable and reproducible.

| File | Role |
|---|---|
| `tribunal/realdata.py` | loads the slim parquet + closed cases + case pack; device/region indexes; fraud-memory; **specific-vs-generic device-profile** rule |
| `tribunal/signals.py` | evidence probes for the five patterns + shared-device rings + known-fraud links + recurring-charge (R7) |
| `tribunal/belief.py` | log-odds belief; `update(LR)` |
| `tribunal/policy_real.py` | the Fraud Policy: actions, approval routes, SAR criteria (3a), thresholds R1–R10, §6 |
| `tribunal/investigator.py` | the loop: probes → belief → pattern → simulated response → initial/final NBA → SAR → answer JSON |
| `tribunal/narrate_real.py` | FinCEN-style SAR narrative (5W1H) + case summary |
| `run_cases.py` | run all 20 → `cases/HHG-XXX.json` |
| `app/streamlit_app.py` | analyst console (demo) |
| `graph/` | TigerGraph schema + GSQL queries + loader for the production/graded path |

### Key modelling decisions (from reading the data)
- **Investigate at `customer_id`** — `card1` is 1:1 with customer; the `-K1/-K2` card_id suffix isn't reconstructable from transactions, so it's used as a label only.
- **The risk score is evidence, not the answer** — belief starts at a 0.25 base rate; a high score is a mild nudge. Half the cases resolve as legitimate, as the README warns.
- **Device profiles: specific vs generic** — a generic OS string ("Windows | …", shared by hundreds) never links accounts; a specific hardware/build string ("SM-G935F Build/… | Android 7.0 | …") does. Many cards on one such profile in a 30-day window is a device farm (→ `undocumented` / R6/R9).
- **A fraud verdict needs ≥2 independent inculpatory signals** (policy §6); single-signal cases go to VERIFY/ESCALATE under R1/R8.

## Results on the 20 cases
9 legitimate · 7 fraud · 4 uncertain · 5 SARs — including HHG-014 (analyst-flagged **device ring → undocumented pattern**) and HHG-010 (risk alert where the NBA evolves **VERIFY → BLOCK** as the simulated customer denial moves confidence 61% → 93%).

---

## TigerGraph (the mandatory tech)

The investigation currently runs on a pandas graph-client over the real data (fast to
iterate, fully offline). `graph/` contains the TigerGraph path to load the same data and
run the same evidence as GSQL:

```bash
# TigerGraph Community Edition 4.2+ (graph + native vectors, free), TG_* in .env
python graph/install.py --reset    # schema + load + install GSQL queries
```

- **Schema** (`graph/schema.gsql`) follows the dataset's suggested schema: `Customer, Card, Transaction, DeviceProfile, EmailDomain, BillingRegion, ClosedCase` + edges, with a native **vector attribute** on `ClosedCase` for hybrid case-memory retrieval (TG 4.2+).
- **Evidence queries** (`graph/queries/`) map 1:1 to `signals.py` (device-neighbours, card-window, region-history, path-to-known-fraud, `vectorSearch` over closed cases).
- **MCP**: expose the installed queries via `tigergraph-mcp` (`run_installed_query`, `search_top_k_similarity`) so the agent calls the graph as tools.
- **Write-back**: each finished case becomes a graph vertex — the memory the next case retrieves.

> The load path is provided and mirrors the pandas client; finalize the vector upsert on
> a live 4.2+ instance. Load the closed-case narratives, the policy, and the FinCEN/FATF
> references into TigerGraph vector search for GraphRAG grounding.

## Answer format
Each `cases/HHG-XXX.json` has the three required parts — `case` (status, verdict,
fraud_probability, pattern, affected_txn_ids, connected_card_ids, exposure, evidence with
citations, similar_prior_cases, summary, written_to_graph), `evidence_requests`,
`next_best_actions.initial/final` + `what_changed`, and `sar` — plus `stop_reason`,
`tool_calls`, `tokens`, `latency_s`. All IDs are real dataset IDs.

## Limitations (honest)
- Likelihood ratios are hand-calibrated against the closed-case base rates, not learned; the closed cases are the place to tune them further.
- Customer/step-up responses are simulated (as the challenge requires); the assumption is recorded in `evidence_requests` and driven by the graph evidence.
- The TigerGraph load path needs a live 4.2+ instance to finalize the vector upsert.
