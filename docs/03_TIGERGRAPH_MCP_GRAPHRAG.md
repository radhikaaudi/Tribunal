# 3. TigerGraph, MCP, GraphRAG — explained simply

The challenge says we **must** use three things: **TigerGraph**, **TigerGraph MCP**, and
**GraphRAG**. Here is what each one is, in plain words, and how our Tribunal uses it.

---

## First: what is a "graph database"?

A normal database is like a big **spreadsheet** — rows and columns. It's great for "show me
payment #123", but terrible for "show me everyone connected to this device, 3 steps away."

A **graph database** stores **dots and lines** instead:

```
   Customer ──owns──> Card ──made──> Payment ──from──> Device
                                        │
                                        └──billed in──> Region
```

To answer *"which other cards used this same device?"* a spreadsheet has to scan everything
many times. A graph just **follows the lines** — instantly. Fraud is all about connections
(rings, shared devices, shared locations), so a graph is the perfect tool. This is the whole
reason the challenge is built on a graph.

---

## 1. TigerGraph — the graph database (the "brain's memory")

**What it is:** TigerGraph is a very fast graph database. It stores all our dots and lines
(customers, cards, payments, devices, regions, and old solved cases) and can answer
connection questions in milliseconds, even over millions of payments.

**How we use it:**

- **Store the map of everything.** We load the payments and identity data as a graph
  (see `graph/schema.gsql`). Cards, devices, regions and email domains become dots; payments
  connect them.
- **Run the Prosecutor & Defender questions as graph queries.** Each "probe" from doc 2 is a
  small graph query written in **GSQL** (TigerGraph's query language). Examples:
  - *"How many other cards used this exact device in the last 30 days?"* → finds rings.
  - *"Is there a path from this customer to a known-fraud case?"* → link to known fraud.
  - *"What are this customer's normal regions / merchants / amounts?"* → defender evidence.
- **Store our memory with `vectors` for similarity search.** Every old solved case gets a
  little "fingerprint" (a list of numbers called a **vector**). TigerGraph can then answer
  *"find me the past cases that look most like this new one"* using **vector search** — all
  inside the same database as the graph. (More on this in the GraphRAG section.)
- **Write our new cases back.** When the Tribunal finishes a case, we save it back into
  TigerGraph as a new dot. So next time, it becomes part of the memory too — the system
  **learns**.

A tiny taste of GSQL (don't worry about the exact syntax):

```gsql
// how many OTHER cards used this device? (the ring probe)
SELECT other FROM Device:d -(FROM_DEVICE)- Transaction -(MADE)- Card:other
WHERE d.profile == $device AND other != $thisCard;
```

> **Where we are right now (honest note):** to build fast, our probes currently run the same
> logic in Python over the slimmed data (`clara/realdata.py`), which mirrors the graph exactly.
> The TigerGraph schema and loader are written (`graph/`) so you load the same data into a real
> TigerGraph instance and flip one switch to run against it. The *design* is graph-first; the
> Python layer is a stand-in during development.

---

## 2. TigerGraph MCP — the "USB port" between the AI and the graph

**MCP = Model Context Protocol.** Think of it as a **standard USB port** for AI agents.

An AI agent (the LLM) can't magically reach into a database. It needs **tools** it's allowed
to call. MCP is the standard way to expose those tools. TigerGraph ships an MCP server that
turns graph actions into neat tools the agent can use, like:

- `run_installed_query` → "run this graph query and give me the result"
- `search_top_k_similarity` → "find the 3 most similar past cases"
- `get_graph_schema` → "tell me what dots and lines exist"

**Why it matters (in plain words):**
- The AI **never writes raw database code or touches passwords.** It just says "run the
  *shared-device* tool for this card," and MCP does it safely. No hallucinated IDs, no leaks.
- It cleanly **separates thinking from doing.** The LLM *reasons* ("I should check for a ring");
  MCP + TigerGraph *do the exact query* and hand back real numbers.

**How we use it:** our Prosecutor and Defender don't talk to the database directly — they call
graph queries **through MCP tools**. In the code, `clara/graph_client.py` is written with two
"plugs": a local one (for development) and a **TigerGraph/MCP** one (for the real run). The rest
of the Tribunal doesn't change — it just asks for evidence, and MCP fetches it from TigerGraph.

*(Analogy: the agent is a chef who shouts orders; MCP is the waiter who safely brings exactly
the right dish from the kitchen (TigerGraph). The chef never runs into the kitchen.)*

---

## 3. GraphRAG — giving the AI the *right* evidence, not raw data

**RAG = Retrieval-Augmented Generation.** Fancy name, simple idea: before the AI writes its
answer, we **fetch the relevant facts and hand them over**, instead of dumping everything or
letting it guess. It's like giving a student the **exact right pages** before an open-book exam.

**GraphRAG** = the same idea, but the facts come from the **graph** (connected evidence) *and*
from **documents** (the fraud policy, the FinCEN report-writing rules, the old case notes).

**How we use it — two ways:**

1. **Connected evidence, not raw rows.** When the Judge writes its explanation, we don't give
   the LLM a giant table of 590,000 payments. We give it the **small, relevant sub-graph** the
   probes found: "this device, these 27 connected cards, this link to case CC-1234." The LLM
   reasons over *evidence*, so it stays accurate and can't wander.

2. **Memory + rulebook retrieval.**
   - **Similar past cases:** we use vector search (in TigerGraph) to pull the **3 most similar
     old cases** and their outcomes, and hand those to the Judge as memory
     ("cases like this were confirmed fraud and blocked").
   - **Policy & report rules:** we retrieve the exact policy rule that applies (e.g., *R2:
     customer denied → block + report*) and the FinCEN guidance for writing the fraud report,
     so the Judge's decision and the report are **grounded in real documents**, with the rule
     number cited.

*(Analogy: instead of throwing the whole library at the AI, GraphRAG hands it the three case
files and the one rulebook page that actually matter.)*

---

## How the three fit together (the full picture)

```
              ┌──────────────────────── the AI agent (LLM) ────────────────────────┐
              │  Prosecutor  ·  Defender  ·  Judge  (reasoning + writing only)      │
              └───────────────▲───────────────────────────────▲────────────────────┘
                              │ asks for evidence via TOOLS    │ gets the RIGHT facts
                         ┌────┴─────┐                     ┌────┴──────────┐
                         │   MCP    │  (the safe USB port) │   GraphRAG    │
                         └────┬─────┘                     └────┬──────────┘
                              │ runs graph queries             │ pulls sub-graph + memory + policy
                         ┌────▼─────────────────────────────────▼────┐
                         │                TigerGraph                  │
                         │  graph (dots+lines) + vectors (memory) +   │
                         │  documents (policy, past-case notes)       │
                         └────────────────────────────────────────────┘
```

In one sentence:

> **TigerGraph** holds the connected data and the memory.
> **MCP** is the safe doorway the AI uses to ask TigerGraph questions.
> **GraphRAG** makes sure the AI only ever sees the *relevant* connected evidence, past cases,
> and policy — so its decisions are accurate, explainable, and grounded.

And the AI itself never *decides* alone — it gathers evidence (through MCP) and explains it
(with GraphRAG), while the **rulebook (policy)** makes the final call. Safe, auditable, and
exactly what a real bank needs.

---

👈 Back to **[01_THE_BIG_IDEA.md](01_THE_BIG_IDEA.md)** · **[02_HOW_WE_BUILT_IT.md](02_HOW_WE_BUILT_IT.md)**
