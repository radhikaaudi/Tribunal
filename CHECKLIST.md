# Tribunal — Submission Checklist

> **⏰ Deadline: Sept 24, 2026, 11:59 PM IST. One submission per team, no resubmissions.**
> Submit here: https://forms.gle/yxXzqSULGgZ9VUF56

This file lists **exactly what is done** and **exactly what only you can do**. Everything
in Part A runs today, offline, with no account and no API key. Part B is your to-do list.

---

## Part A — Already built & working (offline, verified)

You can run and demo all of this right now (`streamlit run app/streamlit_app.py`):

- ✅ **Agent investigation loop** — trigger → investigate → gather evidence → assess
  uncertainty → request more evidence → recommend next-best-action → explain → update memory.
- ✅ **Fraud pattern detection** — card-testing, CNP, new-device, out-of-region, account-takeover,
  shared-device rings, and `undocumented` (device farms). 20/20 cases produce answer files.
- ✅ **Next-best-action** with approval routes (auto/L1/L2), evolving initial → final as evidence arrives.
- ✅ **Uncertainty handling** — log-odds belief, "verify before block" (R1), stops at 0.85/0.15 (§6).
- ✅ **Case creation & progression** — status history + an auditable event timeline per case
  (`case_progression` in every answer file).
- ✅ **Case memory** — retrieves similar prior cases, recognises recurring device profiles / cards
  across cases, and writes each resolved case back (grows as cases close).
- ✅ **GraphRAG grounding** — every recommendation cites retrieved fraud-policy rules, typologies,
  and FinCEN/FATF references (`graphrag` in every answer file). Runs offline via a TF-IDF index.
- ✅ **LLM reasoning layer** — tool selection + evidence synthesis + explanation, provider-agnostic
  (`none` works with no key; add Anthropic/OpenAI to enable the model).
- ✅ **SAR** — FinCEN 5W1H narrative, filed only when policy 3a calls for it (5 of 20 cases).
- ✅ **Analyst UI** — Streamlit console with tabs for reasoning, GraphRAG, case progression, memory,
  evidence, next-best-action, SAR, and the raw submission JSON.
- ✅ **TigerGraph code path** — schema (`graph/schema.gsql`), loader (`graph/install.py`), GSQL
  evidence + write-case queries (`graph/queries/`), a **TigerGraph MCP client**, and a live
  graph write-back — all written and ready; they just need your instance to run against.

---

## Part B — Your to-do list (only you can do these)

### 1. ☐ Create a free TigerGraph instance  *(required component)*
- **Savanna (easiest):** https://savanna.tgcloud.io → create a workspace. **Enable auto-stop/auto-start.**
- **or Community Edition:** https://dl.tigergraph.com → install locally (4.2+ for native vectors).
- Note the host, username, password (and secret/token if Savanna).
- *(I cannot create the account for you — it needs your identity.)*

### 2. ☐ Create your `.env`
```bash
cp .env.example .env
```
Then edit `.env`:
- `TRIBUNAL_GRAPH_BACKEND=tigergraph`   (or `tigergraph_mcp` to go through MCP)
- `TG_HOST`, `TG_USERNAME`, `TG_PASSWORD`, `TG_SECRET` from step 1
- (optional) `TRIBUNAL_LLM_PROVIDER=anthropic` + `ANTHROPIC_API_KEY=...` to turn on LLM reasoning
- (optional) `TG_MCP_URL=...` if you run the MCP server as a separate gateway

### 3. ☐ Load the graph
```bash
python graph/install.py --reset
```
Loads the schema, data, card-to-card links, Case vertices + `sig_vec` native vectors, and installs
the GSQL evidence queries. Watch for the `>> vectors: upserted sig_vec for N/N` line.

### 4. ☐ Stand up TigerGraph MCP  *(required component)*
- Follow https://github.com/tigergraph/tigergraph-mcp to run the MCP server against your instance.
- Point Tribunal at it with `TG_MCP_URL` in `.env` and `TRIBUNAL_GRAPH_BACKEND=tigergraph_mcp`.
- The agent then calls the graph as MCP tools (`run_installed_query`, `search_top_k_similarity`).

### 5. ☐ (Optional but recommended) Turn on the LLM
Add `ANTHROPIC_API_KEY` (or OpenAI) to `.env`. This enables model-written reasoning + SAR polish.
The verdicts stay deterministic either way.

### 6. ☐ Regenerate the 20 answer files against the graph
```bash
python run_cases.py        # -> cases/HHG-XXX.json  (20 files, submission format)
```
Confirm 20 files, verdicts, SARs, and that `graph_writeback` reads `tigergraph`.

### 7. ☐ Record the 3–5 minute demo video
Use `docs/DEMO_SCRIPT.md` as the shot list. Show: a legit case cleared, HHG-010 (VERIFY→BLOCK),
HHG-014 (device ring → SAR), the GraphRAG + memory tabs, and one case written to the graph.

### 8. ☐ Publish the technical blog post
A full draft is in `docs/BLOG.md` — read it, make it yours, publish (Medium/Hashnode/dev.to/LinkedIn).

### 9. ☐ Post on X or LinkedIn
Short post about what you built + link to the blog/demo. **Tag @TigerGraphDB.** (Draft in `docs/BLOG.md`.)

### 10. ☐ Push to GitHub and submit
```bash
git add -A && git commit -m "Tribunal: agentic fraud investigation on TigerGraph"
git push
```
Then submit the GitHub link, the 20 answer files, the demo video, and the blog link at
https://forms.gle/yxXzqSULGgZ9VUF56 (team lead, one submission).

---

## Part C — Required components status

| Required component | Status | What's left |
|---|---|---|
| TigerGraph Savanna/CE (graph + vector) | Code ready | **You:** create instance, run `install.py` (steps 1–3) |
| GSQL + graph algorithms | Written & installable | Runs when you load the graph |
| TigerGraph MCP | Client implemented | **You:** run the MCP server, set `TG_MCP_URL` (step 4) |
| GraphRAG | ✅ Working offline | Auto-uses TG vectors once loaded |
| User interface | ✅ Working | — |
| LLM (optional) | ✅ Provider-agnostic | **You:** add a key to enable (step 5) |
| Agent framework (optional) | Custom implementation | — |

---

## Part D — Command cheat-sheet
```bash
# offline demo (no account, no key) — works now
python run_cases.py
streamlit run app/streamlit_app.py            # http://localhost:8501

# live TigerGraph path (after steps 1–4)
cp .env.example .env                          # then fill in TG_* + backend
python graph/install.py --reset
python run_cases.py
```
