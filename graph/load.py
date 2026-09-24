"""
Create the DefAttack schema on TigerGraph (Savanna / 4.x), load the dataset, install queries.

    .venv/bin/python graph/load.py                 # schema (if missing) + subset load + install queries
    .venv/bin/python graph/load.py --reset         # DROP GRAPH CLARA first, then the above
    .venv/bin/python graph/load.py --full          # load all 590k transactions (slow over REST)
    .venv/bin/python graph/load.py --skip-load     # schema + queries only
    .venv/bin/python graph/load.py --skip-install  # don't INSTALL queries (tg.py falls back to INTERPRET)
    .venv/bin/python graph/load.py --dry-run       # build the subset + counts offline, no TigerGraph

Subset (default): every transaction of the 20 case-pack customers; every transaction that
shares a *specific* device profile with any of them (DeviceInfo not generic and profile used
by <=150 distinct customers); every transaction referenced by a closed case. All 5,565
ClosedCase vertices with CC_ON_CARD / CC_CONNECTED_TO, CC_INVOLVES to loaded txns, and
PolicyDoc chunks (policy rules, patterns, analyst-note templates) for GraphRAG.

Card mapping: transactions carry no card id. A txn named in a closed case / the case pack
gets that case's card_id; everything else maps to <customer_id>-K1.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
DS = os.path.join(ROOT, "dataset")
HERE = os.path.dirname(os.path.abspath(__file__))

GENERIC_DEVICE = {"Windows", "MacOS", "iOS Device", "Linux", "?", "nan", ""}
HUB_CAP = 150
BATCH = 2000
QUERIES = ["device_neighbors", "customer_history", "prior_cases_for_customer",
           "link_to_known_fraud", "policy_search", "similar_investigations"]


# ----------------------------------------------------------------------------- data prep
def _s(x) -> str:
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return ""
    if isinstance(x, float) and x.is_integer():
        return str(int(x))
    s = str(x)
    return "" if s in ("nan", "None", "<NA>") else s


def build(full: bool = False) -> dict:
    t = time.time()
    df = pd.read_parquet(os.path.join(DS, "_slim.parquet"))
    cc = pd.read_csv(os.path.join(DS, "closed_cases_history.csv"))
    cp = pd.read_csv(os.path.join(DS, "case_pack.csv"))
    df["device_profile"] = df["device_profile"].fillna("").astype(str)
    df["tid"] = df["TransactionID"].astype(str)

    prof = df[df.device_profile != ""]
    n_cust = prof.groupby("device_profile").customer_id.nunique()
    di = df["DeviceInfo"].fillna("").astype(str)
    generic_prof = set(prof[di[prof.index].isin(GENERIC_DEVICE)].device_profile)

    cc_tx = cc.assign(tid=cc.txn_ids.fillna("").astype(str).str.split("|")).explode("tid")
    cc_tx = cc_tx[cc_tx.tid != ""]

    if full:
        sub = df
    else:
        cust = set(cp.customer_id)
        own = df.customer_id.isin(cust)
        profs = set(df.loc[own & (df.device_profile != ""), "device_profile"])
        profs = {p for p in profs if p not in generic_prof and n_cust.get(p, 0) <= HUB_CAP}
        sub = df[own | df.device_profile.isin(profs) | df.tid.isin(set(cc_tx.tid))]
    sub = sub.copy()

    # card mapping
    card_of = {}
    for _, r in cc_tx.iterrows():
        card_of[r.tid] = r.card_id
    for _, r in cp.iterrows():
        card_of[str(r.flagged_txn_id)] = r.card_id
    sub["card_id"] = [card_of.get(t, f"{c}-K1") for t, c in zip(sub.tid, sub.customer_id)]
    sub = sub.sort_values("ts")

    m_cols = [f"M{i}" for i in range(1, 10)]
    mflags = sub[m_cols].fillna("-").astype(str).agg("".join, axis=1) if len(sub) else []

    V, E = {}, {}
    V["Transaction"] = [
        (r.tid, {"customer_id": r.customer_id, "card_id": r.card_id,
                 "ts": r.ts.strftime("%Y-%m-%d %H:%M:%S"), "amount": float(r.TransactionAmt),
                 "product": _s(r.ProductCD), "channel": _s(r.channel), "risk": float(r.risk_score or 0),
                 "addr1": _s(r.addr1), "addr2": _s(r.addr2), "p_email": _s(r.P_emaildomain),
                 "r_email": _s(r.R_emaildomain), "device_profile": r.device_profile,
                 "device_type": _s(r.DeviceType), "device_new": _s(r.id_15), "proxy_type": _s(r.id_23),
                 "m_flags": mf})
        for r, mf in zip(sub.itertuples(index=False), mflags)]

    # cards + customers
    card_meta = sub.groupby("card_id").agg(network=("card4", "first"), ctype=("card6", "first"))
    cards = set(sub.card_id) | set(cc.card_id) | set(cp.card_id)
    conn_cards = cc.assign(k=cc.connected_card_ids.fillna("").astype(str).str.split("|")).explode("k")
    conn_cards = conn_cards[conn_cards.k != ""]
    cards |= set(conn_cards.k)
    V["Card"] = []
    for k in sorted(cards):
        m = card_meta.loc[k] if k in card_meta.index else None
        V["Card"].append((k, {"customer_id": k.split("-")[0],
                              "network": _s(m.network) if m is not None else "",
                              "card_type": _s(m.ctype) if m is not None else ""}))
    cust_cards = pd.Series([k.split("-")[0] for k in cards]).value_counts()
    V["Customer"] = [(c, {"n_cards": int(n)}) for c, n in cust_cards.items()]
    E["OWNS"] = [(k.split("-")[0], k) for k in cards]
    E["MADE"] = list(zip(sub.card_id, sub.tid))

    # devices / email / region
    dsub = sub[sub.device_profile != ""]
    V["DeviceProfile"] = []
    for p in sorted(set(dsub.device_profile)):
        parts = (p.split(" | ") + ["", "", "", ""])[:4]
        V["DeviceProfile"].append((p, {"device_info": parts[0], "os": parts[1], "browser": parts[2],
                                       "screen": parts[3], "n_customers": int(n_cust.get(p, 0)),
                                       "is_generic": p in generic_prof}))
    E["FROM_DEVICE"] = list(zip(dsub.tid, dsub.device_profile))
    em = sub[sub.P_emaildomain.notna()]
    V["EmailDomain"] = [(d, {}) for d in sorted(set(em.P_emaildomain.astype(str)))]
    E["PURCHASER_EMAIL"] = list(zip(em.tid, em.P_emaildomain.astype(str)))
    ad = sub[sub.addr1.notna()]
    reg = ad.groupby(ad.addr1.map(_s)).addr2.first()
    V["BillingRegion"] = [(a, {"country": _s(c)}) for a, c in reg.items()]
    E["BILLED_IN"] = list(zip(ad.tid, ad.addr1.map(_s)))

    # NEXT within card
    nxt = []
    for _, g in sub.groupby("card_id"):
        ids, ts = g.tid.tolist(), g.ts.tolist()
        for i in range(1, len(ids)):
            nxt.append((ids[i - 1], ids[i], {"gap_s": int((ts[i] - ts[i - 1]).total_seconds())}))
    E["NEXT"] = nxt

    # closed cases
    def dt(x):
        return _s(x) or "1970-01-01 00:00:00"
    V["ClosedCase"] = [
        (r.case_id, {"customer_id": r.customer_id, "card_id": r.card_id, "opened_at": dt(r.opened_at),
                     "closed_at": dt(r.closed_at), "outcome": r.outcome, "pattern": _s(r.pattern),
                     "first_fraud_txn": _s(r.first_fraud_txn_id), "n_txns": int(r.n_txns or 0),
                     "exposure": float(r.exposure_usd or 0), "actions": _s(r.actions_taken),
                     "report_filed": _s(r.report_filed), "notes": _s(r.analyst_notes)})
        for r in cc.itertuples(index=False)]
    E["CC_ON_CARD"] = list(zip(cc.case_id, cc.card_id))
    E["CC_CONNECTED_TO"] = list(zip(conn_cards.case_id, conn_cards.k))
    loaded = set(sub.tid)
    inv = cc_tx[cc_tx.tid.isin(loaded)]
    E["CC_INVOLVES"] = list(zip(inv.case_id, inv.tid))

    V["PolicyDoc"] = policy_chunks(cc)
    print(f"[build] {'FULL' if full else 'subset'} prepared in {time.time() - t:.1f}s")
    return {"V": V, "E": E}


EDGE_TYPES = {  # edge -> (from, to)
    "OWNS": ("Customer", "Card"), "MADE": ("Card", "Transaction"),
    "FROM_DEVICE": ("Transaction", "DeviceProfile"), "PURCHASER_EMAIL": ("Transaction", "EmailDomain"),
    "BILLED_IN": ("Transaction", "BillingRegion"), "NEXT": ("Transaction", "Transaction"),
    "CC_INVOLVES": ("ClosedCase", "Transaction"), "CC_ON_CARD": ("ClosedCase", "Card"),
    "CC_CONNECTED_TO": ("ClosedCase", "Card"),
}


def policy_chunks(cc: pd.DataFrame) -> list:
    """Split README policy + patterns into retrievable chunks; add analyst-note templates."""
    text = open(os.path.join(DS, "README.md"), encoding="utf-8").read()
    chunks = []

    def add(cid, section, kind, body):
        chunks.append((cid, {"section": section, "kind": kind, "text": body.strip()}))

    pat = text.split("## The five known fraud patterns", 1)[1].split("\n## ", 1)[0]
    for m in re.finditer(r"\*\*(\d)\. ([^*]+?)\.\*\*(.*?)(?=\n\*\*\d\.|\Z)", pat, re.S):
        add(f"PATTERN-{m.group(1)}", m.group(2).strip(), "pattern", f"{m.group(2)}. {m.group(3)}")
    pol = text.split("# Fraud Policy", 1)[1].split("# Answer Format", 1)[0]
    for m in re.finditer(r"\*\*(R\d+)\. ([^*]+)\*\*(.*?)(?=\n\n)", pol, re.S):
        add(f"POLICY-{m.group(1)}", m.group(1), "policy_rule", f"{m.group(1)}. {m.group(2)}{m.group(3)}")
    for m in re.finditer(r"### (\d[ab]?)\. ([^\n]+)\n(.*?)(?=\n### |\n---|\Z)", pol, re.S):
        sec = m.group(1)
        if sec == "3":   # rules already chunked individually
            continue
        add(f"POLICY-{sec}", sec, "policy_section", f"{sec}. {m.group(2)}\n{m.group(3)}")
    add("README-THINGS-TO-KNOW", "things_to_know", "guidance",
        text.split("## Things to know", 1)[1].split("\n## ", 1)[0])

    # analyst-note templates (distinct phrasings), a few per pattern, with an example case id
    def tmpl(s):
        s = re.sub(r"[^.]*came from a [^.]*?\.(?= [A-Z]|$)", " <DEVICE>.", s)
        s = re.sub(r"CC-\d+", "<CASE>", s)
        s = re.sub(r"C\d{5}-K\d", "<CARD>", s)
        s = re.sub(r"C\d{5}", "<CUST>", s)
        s = re.sub(r"\d{4}-\d{2}-\d{2}", "<DATE>", s)
        s = re.sub(r"\$[\d,]+(\.\d+)?", "<AMT>", s)
        return re.sub(r"\b\d+(\.\d+)?\b", "<N>", s)
    c = cc.assign(t=cc.analyst_notes.fillna("").map(tmpl))
    g = (c.groupby("t").agg(n=("case_id", "size"), pattern=("pattern", "first"),
                            outcome=("outcome", "first"), ex=("case_id", lambda x: "|".join(list(x)[:5])))
         .reset_index().sort_values("n", ascending=False))
    i = 0
    for p, gg in g.groupby("pattern"):
        take = gg if p in ("undocumented", "none") else gg.head(4)
        for r in take.itertuples(index=False):
            i += 1
            add(f"NOTE-{i:03d}", f"notes:{p}", "analyst_note",
                f"[{r.outcome} / {p}; {r.n} closed cases, e.g. {r.ex}] {r.t}")
    return chunks


# ----------------------------------------------------------------------------- TigerGraph
def gsql(conn, cmd: str, quiet=False) -> str:
    out = conn.gsql(cmd)
    out = out if isinstance(out, str) else str(out)
    if not quiet:
        print("  gsql>", cmd.splitlines()[0][:80], "->", out.strip().splitlines()[-1][:160] if out.strip() else "")
    return out


def ensure_schema(conn, graph: str, reset: bool):
    graphs = gsql(conn, "SHOW GRAPH *", quiet=True)
    exists = re.search(rf"Graph {graph}\b", graphs) is not None
    if exists and reset:
        gsql(conn, f"DROP GRAPH {graph}")
        exists = False
    if exists:
        print(f"[schema] graph {graph} exists, keeping it (use --reset to rebuild)")
        return
    gsql(conn, f"CREATE GRAPH {graph}()")
    job = open(os.path.join(HERE, "schema.gsql")).read().replace("FOR GRAPH CLARA", f"FOR GRAPH {graph}")
    out = gsql(conn, f"USE GRAPH {graph}\n{job}\nRUN SCHEMA_CHANGE JOB clara_schema\nDROP JOB clara_schema")
    low = out.lower()
    if "error" in low or "fail" in low or "encountered" in low or "reserved keyword" in low \
            or "local schema change succeeded" not in low and "successfully" not in low:
        print(out)
        raise SystemExit("[schema] schema change failed")
    print("[schema] created")


def upsert_all(conn, data: dict):
    for vt, rows in data["V"].items():
        t, n = time.time(), 0
        for i in range(0, len(rows), BATCH):
            n += conn.upsertVertices(vt, rows[i:i + BATCH])
        print(f"[load] {vt:<16} {n:>7} vertices  ({time.time() - t:.1f}s)")
    for et, rows in data["E"].items():
        s, d = EDGE_TYPES[et]
        rows = [r if len(r) == 3 else (r[0], r[1], {}) for r in rows]
        t, n = time.time(), 0
        for i in range(0, len(rows), BATCH):
            n += conn.upsertEdges(s, et, d, rows[i:i + BATCH])
        print(f"[load] {et:<16} {n:>7} edges     ({time.time() - t:.1f}s)")


def install_queries(conn, graph: str):
    for q in QUERIES:
        body = open(os.path.join(HERE, "queries", f"{q}.gsql")).read().replace("FOR GRAPH CLARA", f"FOR GRAPH {graph}")
        out = gsql(conn, f"USE GRAPH {graph}\n{body}")
        low = out.lower()
    if "error" in low or "fail" in low or "encountered" in low or "reserved keyword" in low \
            or "local schema change succeeded" not in low and "successfully" not in low:
            print(out)
    t = time.time()
    print("[install] INSTALL QUERY ... (can take several minutes on Savanna)")
    out = gsql(conn, f"USE GRAPH {graph}\nINSTALL QUERY {', '.join(QUERIES)}")
    print(out[-800:])
    print(f"[install] done in {time.time() - t:.0f}s")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--skip-load", action="store_true")
    ap.add_argument("--skip-install", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    if a.dry_run:
        data = build(a.full)
        for k, v in {**data["V"], **data["E"]}.items():
            print(f"  {k:<16} {len(v)}")
        return

    from clara import tg
    cfg = tg._cfg()
    if cfg is None:
        raise SystemExit("TG_HOST not set in .env")
    graph = cfg["graphname"]
    conn = tg.connect(cfg, need_token=False)
    print(f"[tg] connected to {cfg['host']} (graph {graph})")
    ensure_schema(conn, graph, a.reset)
    conn.graphname = graph
    try:
        conn.getToken(cfg["gsqlSecret"]) if cfg["gsqlSecret"] else conn.getToken()
    except Exception as e:
        print(f"[tg] getToken failed ({e}); continuing with basic auth")
    if not a.skip_load:
        data = build(a.full)
        upsert_all(conn, data)
        try:
            print("[counts]", conn.getVertexCount("*"))
        except Exception:
            pass
    if not a.skip_install:
        install_queries(conn, graph)


if __name__ == "__main__":
    main()
