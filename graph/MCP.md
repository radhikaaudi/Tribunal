# TigerGraph MCP for DefAttack

The official server is **`pyTigerGraph-mcp`** (https://github.com/tigergraph/tigergraph-mcp),
console script **`tigergraph-mcp`**, stdio transport. It is already installed in `.venv`.

```bash
.venv/bin/pip install pyTigerGraph-mcp "mcp>=1.9,<2"   # mcp 2.x breaks pyTigerGraph-mcp 1.0.1
```

## Configuration (.env)

The server reads `TG_*` variables (and loads `.env` itself):

```
TG_HOST=https://<workspace>.i.tgcloud.io
TG_GRAPHNAME=CLARA
TG_USERNAME=...
TG_PASSWORD=...
TG_SECRET=...            # optional; used to mint REST++ tokens
# Savanna: every port is 443. The raw server does NOT infer this, so either set
TG_RESTPP_PORT=443
TG_GS_PORT=443
TG_TGCLOUD=true
# ...or launch it through our wrapper (below), which normalises these automatically.
```

## Running it

```bash
# wrapper: normalises ports/tgcloud from TG_HOST, then runs the official server over stdio
.venv/bin/python -m clara.mcp_tools --serve
# raw official server
.venv/bin/tigergraph-mcp --env-file .env -v
# smoke test: spawn, list tools, count vertices
.venv/bin/python -m clara.mcp_tools
```

## Claude Code / Claude Desktop

Project `.mcp.json` (repo root, already committed):

```json
{ "mcpServers": { "tigergraph": {
    "command": ".venv/bin/python", "args": ["-m", "clara.mcp_tools", "--serve"] } } }
```

Claude Desktop (`claude_desktop_config.json`) needs absolute paths:

```json
{ "mcpServers": { "tigergraph": {
    "command": "/Users/somya.kedia/Documents/hackathon/Tribunal/.venv/bin/python",
    "args": ["-m", "clara.mcp_tools", "--serve"],
    "cwd": "/Users/somya.kedia/Documents/hackathon/Tribunal" } } }
```

## Tools the agent uses

`clara/mcp_tools.py` spawns the server with the `mcp` SDK and exposes sync calls:

| Agent call | MCP tool |
|---|---|
| `run_installed_query("device_neighbors", {"profile", "t0", "t1"})` | `tigergraph__run_installed_query` (`query_name`, `params`) |
| `run_installed_query("link_to_known_fraud", {"customer"})` | same |
| `run_installed_query("prior_cases_for_customer", {"customer"})` | same |
| `run_installed_query("customer_history", {"customer"})` | same |
| `run_installed_query("policy_search", {"keyword", "k"})` | same |
| `run_installed_query("similar_investigations", {"pattern_name", "k"})` | same |
| `run_query("INTERPRET QUERY () FOR GRAPH CLARA { ... }")` | `tigergraph__run_query` (`query_text`) |
| `call("tigergraph__add_node", ...)` / `add_edges` | write-back alternative |
| `call("tigergraph__get_vertex_count", {"vertex_type": "Transaction"})` | stats |

Other useful tools (65 total): `tigergraph__get_graph_schema`, `tigergraph__get_node`,
`tigergraph__get_neighbors`, `tigergraph__get_node_edges`, `tigergraph__gsql`,
`tigergraph__install_query`, `tigergraph__is_query_installed`, vector tools
(`tigergraph__add_vector_attribute`, `tigergraph__search_top_k_similarity`, ...).

The direct pyTigerGraph path (`clara/tg.py`) runs the same installed queries and falls back
to `INTERPRET QUERY` when a query is not installed yet.

## Loading the graph

```bash
.venv/bin/python graph/load.py --dry-run     # offline: build subset + print counts
.venv/bin/python graph/load.py --reset       # drop/create schema, load subset, install queries
.venv/bin/python graph/load.py --skip-load   # (re)create queries only
.venv/bin/python graph/load.py --full        # all 590k transactions (slow over REST)
```
