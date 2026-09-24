# TigerGraph setup — where every `.env` value comes from

You have two free options. **Savanna** = nothing to install (cloud). **Community Edition** = runs on
your Mac with Docker (predictable default credentials). Pick one.

---

## Option A — TigerGraph Savanna (cloud, recommended, no install)

### A1. Create the account
1. Go to **https://savanna.tgcloud.io** and sign up (Google/GitHub/email). It's free for the hackathon.
2. Verify your email and log in.

### A2. Create a Workgroup + Workspace
1. In the console, create a **Workgroup** (a billing/region container) — pick the region closest to you.
2. Inside it, create a **Workspace**. Choose the **Free / smallest** size and **TigerGraph 4.2+**
   (needed for native vectors). Give it a name (e.g. `tribunal`).
3. Wait until the workspace status is **Running / Ready** (1–3 min).

### A3. Turn on auto-stop / auto-start  ← the thing you asked about
- During workspace creation (or later via the workspace's **⋯ / Settings / Edit**), find
  **"Auto-suspend"** (a.k.a. auto-stop) and set an idle timeout (e.g. 60 min). Enable **auto-start /
  auto-resume** so it wakes on the next connection.
- Why: Savanna bills while running; auto-stop parks it when idle so you don't burn your free credits.
  The challenge explicitly asks you to enable this.

### A4. Get the connection values
Open the workspace and find the **"Connect"** / **"Network"** / **"Tools → GraphStudio"** panel:

| `.env` value | Where to get it (Savanna) |
|---|---|
| `TG_HOST` | The workspace **URL / domain** shown in the Connect panel, e.g. `https://abc123.i.tgcloud.io`. Copy it **with `https://`**. |
| `TG_USERNAME` | The DB username. Default admin is `tigergraph` unless you created another user. |
| `TG_PASSWORD` | The password **you set** when creating the workspace (or reset it in **Admin → Users**). |
| `TG_SECRET` | Create one: open **GraphStudio → Admin Portal → Management → Users → (your user) → Create secret**, or run `CREATE SECRET` in GSQL. Optional — the loader auto-creates one if blank. |
| `TG_GRAPHNAME` | Leave as `TRIBUNAL` (the loader creates this graph). |
| `TG_RESTPP_PORT` / `TG_GSQL_PORT` | On Savanna, leave the defaults; the workspace URL already routes them via HTTPS. If a port is shown in the Connect panel, use that. |

> If you can't find the exact labels, use the workspace's **"Connect via API / pyTigerGraph"** helper —
> Savanna shows a ready-made `TigerGraphConnection(...)` snippet with your host filled in. Copy the host
> and username straight from it.

### A5. Fill in `.env`
```ini
TRIBUNAL_GRAPH_BACKEND=tigergraph
TG_HOST=https://abc123.i.tgcloud.io      # <- your workspace URL
TG_USERNAME=tigergraph
TG_PASSWORD=the-password-you-set
TG_SECRET=                                # optional; loader creates one if blank
TG_GRAPHNAME=TRIBUNAL
```

---

## Option B — Community Edition (local, Docker, predictable defaults)

Best if you have Docker and want fixed, known credentials.

### B1. Get the image
1. Go to **https://dl.tigergraph.com**, pick **Community Edition 4.2+**, get the Docker command/image.
2. Run it (example — use the exact command from the download page):
   ```bash
   docker run -d -p 14240:14240 -p 9000:9000 --name tigergraph <community-edition-image>
   ```
3. Wait ~2 min, then open **http://localhost:14240** (GraphStudio) to confirm it's up.

### B2. Fill in `.env` (defaults already match)
```ini
TRIBUNAL_GRAPH_BACKEND=tigergraph
TG_HOST=http://localhost
TG_USERNAME=tigergraph
TG_PASSWORD=tigergraph      # CE default; change it in GraphStudio → Admin if you want
TG_SECRET=
TG_GRAPHNAME=TRIBUNAL
TG_RESTPP_PORT=9000
TG_GSQL_PORT=14240
```

---

## Then, for either option

```bash
python graph/install.py --reset     # creates schema, loads data, upserts vectors, installs GSQL
python run_cases.py                 # regenerates the 20 answer files against the graph
```
Success looks like: `>> vectors: upserted sig_vec for N/N Case vertices`, then 20 files written and
`graph_writeback: tigergraph` in each answer JSON.

### TigerGraph MCP (required component)
```bash
# in a separate terminal, following https://github.com/tigergraph/tigergraph-mcp
# point it at the same TG_HOST/credentials, then in .env:
TRIBUNAL_GRAPH_BACKEND=tigergraph_mcp
TG_MCP_URL=http://localhost:8000     # the MCP server's URL (whatever it prints)
```

### (Optional) LLM reasoning
```ini
TRIBUNAL_LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...          # from https://console.anthropic.com
ANTHROPIC_MODEL=claude-sonnet-5
```
Leave `TRIBUNAL_LLM_PROVIDER=none` (or an empty key) to run fully offline with template reasoning.

---

## Troubleshooting
- **`Connection refused`** → workspace is suspended (Savanna: open it to auto-start) or Docker container isn't up.
- **`401 / token`** → wrong password, or set `TG_SECRET` and let the loader mint a token.
- **`vector attribute` errors** → your instance is < 4.2; recreate the workspace on 4.2+.
- **Savanna host** must include `https://`; CE host is `http://localhost`.
- Official docs: https://docs.tigergraph.com  ·  pyTigerGraph: https://docs.tigergraph.com/pytigergraph
