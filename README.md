# Tribunal — Agentic Fraud Investigation on TigerGraph

> Built for the **TigerGraph Agentic Fraud Investigation — Hacker House Goa 2026** challenge,
> running on the real HHGOA / IEEE-CIS dataset. **New here? Read [`CHECKLIST.md`](CHECKLIST.md).**

Tribunal takes a case from the case pack, investigates it against a transaction knowledge graph
and the bank's closed cases, works out **what kind of fraud it is (if any)**, **how far it goes**,
and **what to do next under the Fraud Policy** — and it **knows when to stop**. It runs an explicit
uncertainty-reduction loop: a fraud belief (log-odds) starts from a base rate nudged by the risk
score / customer report — *never* the risk score as a verdict — and moves by auditable Bayesian
updates as evidence arrives. Every recommendation is **grounded** in the retrieved fraud policy and
typologies (GraphRAG), it **creates and progresses a case** with a full event timeline, it **uses
case memory** to retrieve prior outcomes and recognise recurring entities, and it files a SAR only
when policy 3a calls for one.

Output: **one `cases/HHG-XXX.json` per case, in the exact submission format**.

---

## Quickstart (offline — no account, no API key)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python data/build_cache.py     # one-time: dataset/*.csv -> slim parquet (~15s)
python run_cases.py            # investigate all 20 cases -> cases/HHG-XXX.json
streamlit run app/streamlit_app.py   # analyst console -> http://localhost:8501
```

Runs fully offline in `mock` mode: pandas knowledge graph, TF-IDF GraphRAG, template narratives.
To run on a live TigerGraph instance (the graded path), follow [`CHECKLIST.md`](CHECKLIST.md).

---

## The agent (core investigation flow)

```
 trigger (risk_score | customer_report | analyst_request)
        │
        ▼  open a case; belief = base rate nudged by the trigger (score = a reason to look)
   ┌──────────────┐   tool selection + evidence probes over the graph   ┌───────────────────────┐
   │ investigator │ ─────────────────────────────────────────────────▶ │ signals.py / GSQL:     │
   │  belief +    │   each fires -> belief.update(LR); timeline logged  │ card-testing, CNP,     │
   │  policy +    │ ◀───────────────────────────────────────────────── │ new-device, region,    │
   │  agent loop  │   stop at 0.85/0.15 with >=2 evidence (§6)          │ shared-device ring,    │
   └──────┬───────┘   R1: verify before block on a weak signal         │ path-to-known-fraud    │
          │                                                             └───────────┬───────────┘
          │  GraphRAG: retrieve policy + typologies + FinCEN/FATF, ground the LLM   │
          │  memory:   recall similar prior cases (vector) + recurring entities     │
          ▼                                                                         │
   policy_real.py (R1–R10, routes, SAR 3a)   ·   agent_llm.py (grounded reasoning)  │
   narrate_real.py (SAR 5W1H)                ·   graphrag.py (retrieval)   realdata.py ─▶ TigerGraph
          │                                                               (pandas | GSQL+vectors+MCP)
          ▼
   case + progression timeline + initial/final NBA + SAR + grounded reasoning + case written to graph
```

**Design rule:** the LLM does **tool selection, evidence synthesis, reasoning and explanation** —
always grounded in retrieved context — but it **never decides**. Every verdict threshold, action,
approval route and SAR is a deterministic function of the evidence: auditable and reproducible.

| File | Role |
|---|---|
| `tribunal/realdata.py` | loads the slim parquet + closed cases + case pack; device/region indexes; fraud-memory; specific-vs-generic device-profile rule |
| `tribunal/signals.py` | evidence probes: five patterns + shared-device rings + known-fraud links + recurring-charge (R7) |
| `tribunal/belief.py` | log-odds belief; `update(LR)` |
| `tribunal/policy_real.py` | the Fraud Policy: actions, approval routes, SAR criteria (3a), thresholds R1–R10, §6 |
| `tribunal/graphrag.py` | **GraphRAG** — retrieve policy/typology/regulatory context (+ connected graph evidence) to ground the LLM |
| `tribunal/memory.py` | **case memory** — recall similar prior cases (vector), recurring entities, write resolved cases back |
| `tribunal/agent_llm.py` | **LLM layer** — tool selection + grounded reasoning + explanation (provider-agnostic; `none` runs offline) |
| `tribunal/investigator.py` | the agent loop: probes → belief → pattern → evidence request → NBA → SAR → progression → memory → answer JSON |
| `tribunal/graph_client.py` | one interface, three backends: `MockGraphClient`, `TigerGraphClient`, `TigerGraphMCPClient` |
| `tribunal/narrate_real.py` | FinCEN-style SAR narrative (5W1H) + case summary |
| `graph/` | TigerGraph schema + GSQL evidence/write-case queries + loader (native `sig_vec` vectors) |
| `app/streamlit_app.py` | analyst console: reasoning · GraphRAG · progression · memory · evidence · NBA · SAR · JSON |

### Answer JSON — what each case file contains
`case` (status, verdict, fraud_probability, pattern, affected_txn_ids, connected_card_ids, exposure,
evidence, similar_prior_cases, summary, written_to_graph, graph_writeback) · `agent_reasoning`
(provider, tool_plan, reasoning, uncertainty, grounded_on) · `graphrag` (retrieved policy/typology/
regulatory citations + graph context) · `case_progression` (status_history + event timeline) ·
`case_memory` (retrieved priors + recurring entities) · `evidence_requests` · `next_best_actions`
(initial/final + what_changed) · `sar` · `stop_reason` · `belief_trajectory` · `tool_calls` · `tokens`.

---

## How Tribunal meets the required components

| Required component | How | Where |
|---|---|---|
| **TigerGraph** (graph + vector) | schema, loader, native `sig_vec` HNSW vectors, card-to-card links | `graph/schema.gsql`, `graph/install.py` |
| **GSQL + graph algorithms** | device-neighbour traversal, card-window velocity, region history, BFS path-to-known-fraud, `vectorSearch` | `graph/queries/evidence.gsql` |
| **TigerGraph MCP** | agent calls the graph as MCP tools (`run_installed_query`, `search_top_k_similarity`) | `tribunal/graph_client.py::TigerGraphMCPClient` |
| **GraphRAG** | retrieve policy/typology/FinCEN-FATF context + connected graph evidence, pass to the LLM (not raw data) | `tribunal/graphrag.py` |
| **User interface** | Streamlit analyst console (8 tabs) | `app/streamlit_app.py` |
| LLM (optional) | reasoning/tool-selection/synthesis; provider-agnostic | `tribunal/agent_llm.py` |

The offline `mock` backend mirrors the TigerGraph path 1:1 (same evidence, same signatures), so you
can build and demo everything today; flipping `TRIBUNAL_GRAPH_BACKEND=tigergraph` (or `tigergraph_mcp`)
routes the identical investigation through the live graph. See [`CHECKLIST.md`](CHECKLIST.md).

### Key modelling decisions
- **Investigate at `customer_id`** — `card1` is 1:1 with customer; the `-K1/-K2` suffix is a label only.
- **The risk score is evidence, not the answer** — belief starts at a 0.25 base rate; a high score is a mild nudge.
- **Device profiles: specific vs generic** — a generic OS string never links accounts; a specific hardware/build string does. Many cards on one such profile in a 30-day window is a device farm (→ `undocumented`, R6/R9).
- **A fraud verdict needs ≥2 independent inculpatory signals** (§6); single-signal cases go to VERIFY/ESCALATE (R1/R8).

## Results on the 20 cases
10 legitimate · 10 fraud · 5 SARs — including HHG-014 (analyst-flagged **device ring → undocumented
pattern + SAR**) and HHG-010 (risk alert where the NBA evolves **VERIFY → BLOCK** as the simulated
customer denial moves confidence). Verdicts are deterministic and reproducible.

## Limitations (honest)
- Likelihood ratios are hand-calibrated against the closed-case base rates, not learned.
- Customer/step-up responses are simulated (as the challenge allows); the assumption is recorded in `evidence_requests`.
- The live TigerGraph path (schema/loader/queries/MCP) is written and ready but is verified only on your own instance — see `CHECKLIST.md` steps 1–4. Offline mode is fully verified.
