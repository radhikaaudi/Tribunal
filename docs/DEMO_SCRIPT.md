# Tribunal — 3–5 minute demo script (shot list)

Record with the app running: `streamlit run app/streamlit_app.py` → http://localhost:8501.
Set the sidebar **Animation delay** to ~0.4s so the belief meter visibly moves.

---

### 0:00 — Hook (20s)
> "Fraud analysts spend hours gathering transaction history, tracing connections, checking
> policy, and deciding what to do. Tribunal is an agent that does that investigation on a
> TigerGraph knowledge graph — and, crucially, it knows when it's uncertain and what to do next."

Show the app header + the case list in the sidebar.

### 0:20 — A legitimate case cleared (35s)  →  **HHG-012**
- Select HHG-012 (high risk score, benign). Press **Investigate**.
- Point at the belief meter *staying low* → verdict **legitimate**, action **ALLOW / CLOSE_NO_FRAUD**.
> "A high risk score is a reason to look, not a verdict. The evidence didn't support fraud, so the
> agent clears it — half these cases are false alarms."

### 0:55 — Uncertainty → evidence request → action changes (60s)  →  **HHG-010**
- Select HHG-010. Investigate. Watch the meter climb, then jump on the customer denial.
- Open the **Next-best-action** tab: show **initial VERIFY** → **final BLOCK + FILE_REPORT**, and "what changed".
> "This is the core of the challenge: under uncertainty the agent recommends VERIFY before blocking
> (policy R1). The simulated customer denies the charge, confidence crosses the line, and the
> recommendation upgrades to BLOCK — with the L2 approval route attached."

### 1:55 — Reasoning + GraphRAG grounding (55s)  → stay on HHG-010
- Open **🧠 Reasoning**: read the grounded reasoning + the **tool-selection** table + the uncertainty line.
- Open **📚 GraphRAG**: show the retrieved **policy rule**, **typology**, and **FinCEN/FATF** snippets with citations.
> "The LLM does the reasoning and tool selection, but it's grounded — every recommendation cites the
> actual fraud policy and typologies retrieved by GraphRAG, not raw data. The decision itself stays
> deterministic and auditable."

### 2:50 — Case memory + a new pattern + SAR (60s)  →  **HHG-014**
- Select HHG-014 (analyst-flagged device ring). Investigate.
- Open **🧩 Memory**: show retrieved prior cases + **recurring device profiles across cases**.
- Open **🗂 Case progression**: show the status history + event timeline.
- Open **📄 SAR**: show the filed FinCEN 5W1H narrative.
> "The agent recognises a device farm that matches none of the five documented patterns — it labels
> it 'undocumented', escalates, and files a SAR. Notice it also remembers device profiles recurring
> across cases: that's case memory improving future investigations."

### 3:50 — TigerGraph + close (40s)
- (If live) Show `graph_writeback: tigergraph` in the **JSON** tab, or the `graph/` folder + `install.py`.
> "It all runs on TigerGraph: GSQL evidence queries, native-vector case memory, exposed to the agent
> through TigerGraph MCP, with each resolved case written back as a graph vertex — the memory the next
> investigation retrieves. Twenty cases, one answer file each, in the submission format. That's Tribunal."

---

**Backup cases:** HHG-011 (card-testing → DECLINE+BLOCK), HHG-006 (new-device fraud, $1.9k → SAR).
