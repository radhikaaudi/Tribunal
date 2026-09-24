# DefAttack — Agentic Fraud Investigation

An agentic fraud investigator for the **TigerGraph Agentic Fraud Investigation — Hacker House Goa 2026** challenge, running on the real HHGOA / IEEE-CIS dataset.

> Naming: the application is **DefAttack** (prosecution *attacks* the alert, defence *defends* the cardholder). The Python package is `clara/` and the TigerGraph graph is `CLARA`, from the project's first name.

DefAttack takes an alert (risk score, customer report or analyst request), investigates it over the transaction graph and the bank's closed cases, decides **what kind of fraud it is (if any)**, **how far it goes** and **what to do next under the Fraud Policy**, and **knows when it needs more evidence and when to stop**. The investigation is an explicit uncertainty-reduction loop: a fraud belief (log-odds) is moved by auditable likelihood-ratio updates from a *prosecutor* pass (evidence for fraud) and a *defender* pass (legitimate explanations), then a policy-bound next-best-action is chosen before and after a (simulated) customer / step-up response.

Output: `cases/HHG-001.json … HHG-020.json` in the exact README answer format (case + evidence_requests + next_best_actions.initial/final + sar + stop_reason + tool_calls/tokens/latency).

---

## Quickstart

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt pyarrow

# put the HHGOA files in dataset/  (transactions.csv, identity.csv, closed_cases_history.csv, case_pack.csv, README.md)
python data/build_cache.py            # one-time: 708MB CSV -> dataset/_slim.parquet (~15s)
python run_cases.py                   # investigate all 20 -> cases/HHG-XXX.json (+ memory/agent_cases.jsonl)
python validate_answers.py            # format + policy checks on every answer file
streamlit run app/streamlit_app.py    # analyst console
```

TigerGraph (Savanna or Community Edition 4.x), credentials in `.env` (see `.env.example`):

```bash
python graph/load.py --reset          # schema + subset load (~54k txns, 5,565 closed cases, policy chunks) + install queries
python run_cases.py                   # now also writes every case to the graph (written_to_graph=true)
python -m clara.mcp_tools --serve     # TigerGraph MCP server (pyTigerGraph-mcp) — also wired in .mcp.json
```

Optional LLM for the case summary and SAR prose: `CLARA_LLM_PROVIDER=gemini` + `GEMINI_API_KEY` (Google AI Studio, models `gemini-flash-lite-latest` → `gemini-3-flash-preview` (automatic fallback)), or `anthropic` + `ANTHROPIC_API_KEY`. Calls are paced and retried for free-tier rate limits.

---

## How it works

```
 trigger (risk_score | customer_report | analyst_request)
    │   the risk score opens the case but is NOT evidence (see "calibration" below)
    ▼
 PROSECUTOR probes (graph)            DEFENDER probes (graph)             CONTEXT
 card testing (R5) · structuring      recurring charge (R7) · familiar     customer's closed cases
 CNP burst · new device / proxy       specific device · familiar merchant  agent's own earlier cases
 out-of-region clone vs trip          amount/product in range · tenure     (case memory)
 account takeover · device-ring
 neighbours (R6) · path to known fraud
    │  each finding = claim + ref + entity IDs + likelihood ratio -> Bayesian log-odds update
    ▼
 belief p_graph ──► INITIAL next-best-action (R1: verify / step-up before any block on thin evidence)
    │
    ├─ 0.15 < p < 0.85 → request evidence (customer_validation | step_up_auth); the simulated reply
    │   follows what the graph predicts: denial (net LR ≥ 4) · no reply in 24h (2–4, R4) · confirm (< 2, R3)
    ▼
 p_final ──► verdict (≥2 independent inculpatory findings for fraud, policy 6) → pattern → exposure
    ▼
 FINAL next-best-action + approval route (policy §2) · SAR only under 3a · stop_reason (policy 6)
    ▼
 explanation grounded in policy chunks (GraphRAG) · case written to memory + TigerGraph
```

**Design rule:** decisions (verdict thresholds, actions, routes, SAR) are deterministic functions of the evidence and the policy — auditable and reproducible. The LLM, when enabled, only writes the summary and SAR prose from the retrieved evidence + policy chunks; it cannot change a decision.

| File | Role |
|---|---|
| `clara/investigator.py` | the loop: probes → belief → evidence request → verdict → pattern → initial/final NBA → SAR → answer JSON → write-back |
| `clara/signals.py` | the evidence probes (all anchored in time on the flagged transaction) |
| `clara/realdata.py` | dataset loader, device/customer indexes, closed-case memory + hybrid retrieval |
| `clara/policy_real.py` | action identifiers, approval routes, thresholds, SAR criteria (3a) |
| `clara/knowledge.py` | the document side of GraphRAG: policy rules R1–R10, 3a/3b/§2/§4–6 and the five patterns, chunked from the dataset README |
| `clara/memory.py` | the agent's own case memory: every finished case, retrievable by shared device / card / customer |
| `clara/tg.py` | TigerGraph client: installed GSQL queries, policy search, `write_case` (InvestigationCase vertex + edges) |
| `clara/mcp_tools.py` | TigerGraph MCP client/server wrapper (official `pyTigerGraph-mcp`, stdio) |
| `clara/narrate_real.py`, `clara/llm.py` | SAR narrative (FinCEN 5W1H) · optional Claude summary/SAR polish |
| `graph/schema.gsql`, `graph/queries/*.gsql`, `graph/load.py` | graph schema, GSQL evidence queries, loader |
| `app/streamlit_app.py` | analyst console: live belief meter, prosecution vs defence, evidence request, initial→final NBA, case memory, SAR |
| `validate_answers.py`, `validate_on_closed.py` | answer-format/policy validator · accuracy check against closed cases |

## What we learned from the data (and built into the agent)

- **`customer_id` is an issuer group, not a person.** Some "customers" have 10,000+ transactions across 30+ regions, so per-customer history probes are weak and exact-amount "recurring" matches happen by chance. R7 is only claimed with a same-merchant proxy (non-free-mail domain), same amount, same day-of-month in ≥3 months.
- **The risk score is anti-informative among alerts.** In the closed cases every cleared alert scored > 0.7 (mean 0.88) versus 24% of confirmed frauds (mean 0.47). The score opens an investigation and moves nothing.
- **Two undocumented patterns live in the closed cases** and in the exam: (1) *threshold structuring* — four online purchases in ~40 minutes each just under $500 (CC-3748 …; HHG-006), (2) a *device farm* — one `SM-G935F … Android 7.0` profile behind an anonymous proxy, marked New, used by 28 customers in three weeks (CC-2649 …; HHG-014). Both are detected by probes, described in `pattern_description`, and routed under R9.
- **Generic fingerprints never link accounts.** OS-only DeviceInfo strings and browser engines (`Windows`, `iOS Device`, `Trident/7.0`, `rv:…`) are shared by hundreds; only hardware/build strings form rings.
- **Evidence must be anchored in time.** An earlier version reported "card testing" on HHG-011 from an August episode; probes now look only around the alert, which revealed the real story — a four-card SM-G610F device ring within three days.

## Results on the 20 cases

9 fraud · 7 legitimate · 4 uncertain · 3 SARs. Highlights:

- **HHG-006** — customer report → four purchases just under $500 in 30 min → `undocumented` (threshold structuring), exposure $1,906.07, SAR, R9 escalation.
- **HHG-014** — analyst request → device-neighbour traversal finds a 28-customer device farm linked to four confirmed closed cases → `undocumented`, 27 connected cards monitored (R6), SAR.
- **HHG-011** — customer report → shared device with three other cards in three days, two with confirmed fraud → R6 shared origin → SAR + `MONITOR_CONNECTED_CARDS`.
- **HHG-010 / HHG-015** — high-score, high-exposure online alerts with only a new device + unusual amount: STEP_UP_AUTH first (R1), no reply assumed → `uncertain`, MONITOR + DECLINE (R4) and ESCALATE (R8).
- **HHG-012 / HHG-017** — suspicious-looking alerts that the defender explains (familiar amount/product, habitual purchase size) → verification → cleared under R3.

Case memory in action: after HHG-014 is closed, a new alert on ring member C09906 retrieves HHG-014 from memory and inherits the ring finding.

## Honest limitations

- Likelihood ratios are hand-set. The closed cases cannot calibrate them cleanly: confirmed frauds were mostly customer-reported while cleared cases were all high-score model alerts, so the two populations differ. On 160 closed cases re-run as risk-score alerts (`validate_on_closed.py`), DefAttack clears most historical frauds — without the customer's denial, a single stolen-card purchase usually looks ordinary. That is the case for asking the customer (R1), not for blocking.
- Customer, step-up and analyst responses are simulated (as the challenge requires) and the assumption is recorded in each `evidence_requests` entry together with the graph evidence that motivated it.
- Card-level identity (`-K1` / `-K2`) is not reconstructable from the transaction columns; investigation is at customer level and card IDs come from the case pack / closed cases.
- The pandas probes and the GSQL queries implement the same traversals; the offline run uses pandas, and with TigerGraph configured the agent additionally runs the graph queries (direct REST and via MCP) and writes each case to the graph.

`legacy/` holds the first synthetic-data prototype and is not part of the submission pipeline.
