"""
Provision a real TigerGraph instance for Tribunal and load the data.

  python graph/install.py --reset      # drop + recreate schema, load, install queries

Loads from data/*.csv (synthetic OR the real HHGOA_IEEE files, same columns). Reuses
MockGraphClient's derived signatures/adjacency so the graph matches the mock exactly.

Requires a running TigerGraph 4.2+ and the TG_* vars in .env. This is the "real backend"
path; the mock backend needs none of this.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
import pandas as pd
import pyTigerGraph as tg

from tribunal.graph_client import MockGraphClient, COMMON_EMAIL, GENERIC_DEVICE

load_dotenv()
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(os.path.dirname(HERE), "data")


def connect():
    conn = tg.TigerGraphConnection(
        host=os.getenv("TG_HOST", "http://localhost"),
        username=os.getenv("TG_USERNAME", "tigergraph"),
        password=os.getenv("TG_PASSWORD", "tigergraph"),
    )
    return conn


def run_gsql_file(conn, path):
    with open(path) as f:
        print(conn.gsql(f.read()))


def _upsert_vectors(conn, verts):
    """Upsert native HNSW vectors on Case.sig_vec. Tries the pyTigerGraph batch API
    first, then the TG 4.2 REST vector endpoint; returns how many succeeded so a
    partial/older instance degrades loudly instead of silently."""
    try:
        conn.upsertVertices("Case", verts)
        return len(verts)
    except Exception as e:
        print(f"  batch vertex-vector upsert unavailable ({e}); trying REST /restpp/vector")
    import json
    import urllib.request
    ok = 0
    host = os.getenv("TG_HOST", "http://localhost").rstrip("/")
    port = os.getenv("TG_RESTPP_PORT", "9000")
    token = getattr(conn, "apiToken", None) or os.getenv("TG_TOKEN", "")
    for cid, attrs in verts:
        body = {"vertices": {"Case": {cid: {"sig_vec": {"value": attrs["sig_vec"]}}}}}
        req = urllib.request.Request(
            f"{host}:{port}/restpp/graph/TRIBUNAL", data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json",
                     **({"Authorization": f"Bearer {token}"} if token else {})})
        try:
            urllib.request.urlopen(req, timeout=15)
            ok += 1
        except Exception as e:
            print(f"  vector upsert failed for {cid}: {e}")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true", help="drop graph first")
    args = ap.parse_args()

    conn = connect()
    if args.reset:
        try:
            conn.gsql("USE GRAPH TRIBUNAL DROP GRAPH TRIBUNAL")
        except Exception as e:
            print("drop skipped:", e)

    print(">> schema"); run_gsql_file(conn, os.path.join(HERE, "schema.gsql"))
    conn.graphname = "TRIBUNAL"
    secret = os.getenv("TG_SECRET") or conn.createSecret()
    conn.getToken(secret)

    mock = MockGraphClient(DATA)   # single source of truth for derived features
    txn, idf, cases = mock.txn, mock.idf, mock.cases
    merged = mock.merged

    # ---- Card vertices (risk_max, exposure) ----
    agg = txn.groupby("card1").agg(risk_max=("risk_score", "max"),
                                   exposure=("TransactionAmt", "sum")).reset_index()
    conn.upsertVertexDataFrame(agg, "Card", "card1",
                               attributes={"risk_max": "risk_max", "exposure": "exposure"})

    # ---- Txn vertices (f_flags = count of M*=='F') ----
    mcols = [f"M{i}" for i in range(1, 10)]
    txn = txn.copy()
    txn["f_flags"] = (txn[mcols] == "F").sum(axis=1)
    txn["dist1"] = txn["dist1"].fillna(0)
    conn.upsertVertexDataFrame(
        txn, "Txn", "TransactionID",
        attributes={"dt": "TransactionDT", "amt": "TransactionAmt", "product": "ProductCD",
                    "risk_score": "risk_score", "f_flags": "f_flags", "dist1": "dist1"})

    # ---- attribute vertices ----
    emails = pd.DataFrame({"domain": txn["P_emaildomain"].dropna().unique()})
    emails["is_common"] = emails["domain"].isin(COMMON_EMAIL)
    conn.upsertVertexDataFrame(emails, "Email", "domain", attributes={"is_common": "is_common"})

    devs = pd.DataFrame({"info": merged["DeviceInfo"].dropna().unique()})
    devs["is_generic"] = devs["info"].isin(GENERIC_DEVICE)
    conn.upsertVertexDataFrame(devs, "Device", "info", attributes={"is_generic": "is_generic"})

    addrs = pd.DataFrame({"addr1": txn["addr1"].dropna().unique()})
    conn.upsertVertexDataFrame(addrs, "Addr", "addr1", attributes={})

    # ---- edges ----
    conn.upsertEdgeDataFrame(txn, "Card", "SWIPED", "Txn", "card1", "TransactionID", attributes={})
    conn.upsertEdgeDataFrame(txn.dropna(subset=["P_emaildomain"]), "Txn", "FROM_EMAIL", "Email",
                             "TransactionID", "P_emaildomain", attributes={})
    md = merged.dropna(subset=["DeviceInfo"])
    conn.upsertEdgeDataFrame(md, "Txn", "ON_DEVICE", "Device", "TransactionID", "DeviceInfo", attributes={})
    conn.upsertEdgeDataFrame(txn.dropna(subset=["addr1"]), "Txn", "BILLED_TO", "Addr",
                             "TransactionID", "addr1", attributes={})

    # ---- materialized card-to-card shared links (from mock adjacency) ----
    dev_edges = [{"from": a, "to": b} for a, peers in mock._device_peers.items() for b in peers if a < b]
    if dev_edges:
        conn.upsertEdgeDataFrame(pd.DataFrame(dev_edges), "Card", "SHARES_DEVICE", "Card",
                                 "from", "to", attributes={})
    em_edges = [{"from": a, "to": b} for a, peers in mock._email_peers.items() for b in peers if a < b]
    if em_edges:
        conn.upsertEdgeDataFrame(pd.DataFrame(em_edges), "Card", "SHARES_EMAIL", "Card",
                                 "from", "to", attributes={})

    # ---- Case vertices + sig_vec (native vector) + CASE_OF edges ----
    conn.upsertVertexDataFrame(
        cases, "Case", "case_id",
        attributes={"card1": "card1", "outcome": "outcome", "typology": "typology",
                    "action": "action_taken", "note": "note"})
    conn.upsertEdgeDataFrame(cases, "Case", "CASE_OF", "Card", "case_id", "card1", attributes={})
    # ---- native-vector upsert of each case signature (TG 4.2+ HNSW `sig_vec`) ----
    # Case memory retrieval uses vectorSearch over these; see queries/evidence.gsql.
    verts = [(str(r.case_id), {"sig_vec": mock._case_signatures[int(r.card1)]})
             for r in cases.itertuples()]
    upserted = _upsert_vectors(conn, verts)
    print(f">> vectors: upserted sig_vec for {upserted}/{len(verts)} Case vertices")

    print(">> queries")
    run_gsql_file(conn, os.path.join(HERE, "queries", "evidence.gsql"))
    run_gsql_file(conn, os.path.join(HERE, "queries", "write_case.gsql"))
    print(conn.gsql("USE GRAPH TRIBUNAL INSTALL QUERY ALL"))
    print("done. set TRIBUNAL_GRAPH_BACKEND=tigergraph in .env to use it.")


if __name__ == "__main__":
    main()
