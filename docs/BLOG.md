# Tribunal: an agentic fraud investigator that knows when it's uncertain

*Draft for the HHGOA 2026 technical blog post. Edit freely, then publish and link it in the
submission. Replace the bracketed bits and add your own screenshots from the Streamlit console.*

---

## What we built

Tribunal is an AI agent that investigates fraud alerts the way a human analyst would — but on a
TigerGraph knowledge graph, and with an explicit model of its own uncertainty. Given a trigger (a
risk score, a customer report, or an analyst flag), it opens a case, gathers evidence from the graph,
figures out **what kind of fraud it is (if any)**, decides **what to do next under the bank's policy**,
and **knows when to stop**. It produces one answer file per case in the challenge's submission format,
plus a live analyst console.

The thing we cared most about is the part the challenge cares most about: **handling uncertainty**.
Half the alerts in this dataset are false alarms, and the risk score is "often wrong in both
directions." So Tribunal never treats the score as a verdict — it treats it as a reason to look.

## The architecture

The investigation is an **uncertainty-reduction loop** over a log-odds fraud belief:

1. **Trigger → open a case.** Belief starts at a 0.25 base rate, nudged by the trigger.
2. **Tool selection + evidence gathering.** The agent runs graph probes — card-testing bursts,
   card-not-present anomalies, new-device use, out-of-region use, identity/match-flag anomalies,
   shared-device rings (device-neighbour traversal), and BFS path-to-known-fraud. Each fires a
   likelihood ratio that moves the belief.
3. **Assess uncertainty.** A fraud verdict needs ≥2 independent inculpatory signals (policy §6),
   and it must survive its strongest legitimate explanation. Below 0.70 on a weak signal, the agent
   **verifies before blocking** (R1).
4. **Gather more evidence.** It requests a controlled, policy-approved action — customer validation
   or step-up auth — and updates the belief on the response.
5. **Recommend next-best-action.** Initial and final recommendations, each with an approval route
   (auto / L1 / L2), and it shows what changed between them.
6. **Explain + file a SAR** when policy 3a calls for one (FinCEN 5W1H narrative).
7. **Update memory.** The resolved case is written back — the memory the next case retrieves.

**A design rule we're proud of:** the LLM does the *reasoning, tool selection, evidence synthesis,
and explanation* — but it **never decides**. Every verdict threshold, action, approval route and SAR
is a deterministic function of the evidence. That makes the whole thing auditable and reproducible,
which matters in a compliance setting. The LLM's job is to make the reasoning legible and grounded.

## How we use TigerGraph

- **Knowledge graph.** Customers, cards, transactions, device profiles, email domains, billing
  regions and closed cases, with the edges between them, plus materialised card-to-card shared-device
  and shared-email links.
- **GSQL evidence queries.** Each graph probe maps to an installed GSQL query — device neighbours,
  card-window velocity, region history, path-to-known-fraud, and a `vectorSearch` over closed cases.
- **Native vectors for case memory.** Each closed case carries an HNSW `sig_vec` signature; retrieving
  similar prior cases is a native vector search on the graph (TigerGraph 4.2+).
- **TigerGraph MCP.** The agent reaches the graph as MCP tools (`run_installed_query`,
  `search_top_k_similarity`) rather than a raw driver, so the investigation is genuinely tool-driven.
- **Write-back.** Every resolved case becomes a Case vertex — cross-case memory that grows.

## GraphRAG: grounding, not raw data

A recommendation is only defensible if it cites the rule it follows. So before the agent explains a
decision, it retrieves the relevant **fraud-policy rules, typologies, and FinCEN/FATF references** and
the **connected graph evidence**, and passes *that* to the LLM — not the raw transaction rows. Every
case file records exactly which policy sections and typologies grounded the recommendation.

## What we learned

- **The risk score is a trap.** Modelling belief separately from the score — and forcing ≥2
  independent signals — is what stops the agent from rubber-stamping the model.
- **"Undocumented" is a feature, not a gap.** Some fraud in this data fits none of the five known
  patterns (device farms, for instance). Detecting a dense shared-device cluster and *naming it*
  ourselves, then escalating, scored better than forcing it into a known bucket.
- **Deterministic decisions + LLM explanation** is a better split than "let the LLM decide." We get
  auditability and good prose.

## What we'd improve with more time

- Learn the likelihood ratios from the closed cases instead of hand-calibrating them.
- Replace simulated customer/step-up responses with a real (mock) messaging integration.
- Load the full regulatory corpus into TigerGraph's vector store for richer GraphRAG grounding.
- A graph-native visualisation of the connected-cards subgraph in the console.

## Try it

Repo: [your GitHub link] · Demo video: [your link] · Built on **@TigerGraphDB**.

---

### Social post draft (X / LinkedIn)
> We built **Tribunal** for #HHGOA2026: an agentic fraud investigator on @TigerGraphDB. It
> investigates alerts on a knowledge graph, models its own uncertainty (verify before it blocks),
> grounds every call in policy with GraphRAG, and files SARs only when the rules say so. Deterministic
> decisions, LLM explanations, case memory that grows. [blog/demo link]
