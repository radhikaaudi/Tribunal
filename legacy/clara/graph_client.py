"""
Graph access behind one interface, two implementations:

  MockGraphClient  - computes evidence in pandas from data/*.csv. No TigerGraph needed.
                     Lets you build + demo the whole agent today.
  TigerGraphClient - runs the SAME evidence as installed GSQL queries via pyTigerGraph
                     (and/or TigerGraph-MCP) for the graded submission.

Both expose identical methods, so clara/agent.py never changes when you switch
CLARA_GRAPH_BACKEND from `mock` to `tigergraph`.
"""
from __future__ import annotations

import math
import os
from collections import defaultdict, deque
from typing import Optional

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
COMMON_EMAIL = {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "aol.com"}
# generic device strings behave like hub nodes - linking on them creates false rings
GENERIC_DEVICE = {"Windows", "MacOS", "iOS Device", "rv:11.0"}
DEVICE_HUB_CAP = 15   # skip any attribute value shared by more cards than this (hub)
EMAIL_HUB_CAP = 15
DAY = 24 * 3600


# ---------------------------------------------------------------------------
class GraphClient:
    """Interface. All evidence the agent can ever request is declared here."""

    def prior_and_exposure(self, card1: int) -> tuple[float, float]:
        raise NotImplementedError

    def shared_device_count(self, card1: int) -> int: raise NotImplementedError
    def shared_email_count(self, card1: int) -> int: raise NotImplementedError
    def velocity_24h(self, card1: int) -> int: raise NotImplementedError
    def identity_mismatch_score(self, card1: int) -> float: raise NotImplementedError
    def hops_to_known_fraud(self, card1: int) -> Optional[int]: raise NotImplementedError
    def similar_cases(self, card1: int, k: int = 3) -> list[dict]: raise NotImplementedError

    # Controlled, policy-approved actions (simulated / stubbed per the challenge).
    def ask_customer(self, card1: int) -> str: raise NotImplementedError
    def step_up_auth(self, card1: int) -> str: raise NotImplementedError

    def write_case(self, case_dict: dict) -> None: raise NotImplementedError


# ---------------------------------------------------------------------------
class MockGraphClient(GraphClient):
    def __init__(self, data_dir: str = DATA):
        import pandas as pd
        self.pd = pd
        self.txn = pd.read_csv(os.path.join(data_dir, "train_transaction.csv"))
        self.idf = pd.read_csv(os.path.join(data_dir, "train_identity.csv"))
        self.cases = pd.read_csv(os.path.join(data_dir, "cases.csv"))
        self.merged = self.txn.merge(self.idf, on="TransactionID", how="left")
        # KNOWN fraud = closed confirmed cases only. These are the graph's labeled
        # seeds; a benchmark subject must be *discovered* near them, not pre-labeled.
        self._known_fraud = set(
            self.cases.loc[self.cases.outcome == "confirmed_fraud", "card1"].astype(int).tolist()
        )
        # GROUND TRUTH fraud (whole rings) - used ONLY to simulate the real-world
        # response of customer-validation / step-up actions (stubbed APIs).
        self._true_fraud = (set(range(50001, 50009)) | set(range(60001, 60006))
                            | {70001, 70002, 80001, 80002})
        self._build_shared_graph()
        self._case_signatures = {int(r.card1): self._signature(int(r.card1))
                                 for r in self.cases.itertuples()}

    # -- shared-attribute graph over card1 nodes (device + rare email links) --
    # Only link on HIGH-CARDINALITY, non-hub attribute values. Linking on generic
    # devices or common email domains would fuse unrelated accounts into false rings.
    def _build_shared_graph(self):
        self.adj = defaultdict(set)
        self._device_peers = defaultdict(set)
        self._email_peers = defaultdict(set)

        by_device = defaultdict(set)
        for r in self.merged.dropna(subset=["DeviceInfo"]).itertuples():
            by_device[r.DeviceInfo].add(int(r.card1))
        for dev, cards in by_device.items():
            if dev in GENERIC_DEVICE or len(cards) > DEVICE_HUB_CAP:
                continue
            for a in cards:
                peers = cards - {a}
                self._device_peers[a] |= peers
                self.adj[a] |= peers

        by_email = defaultdict(set)
        for r in self.txn.itertuples():
            dom = getattr(r, "P_emaildomain")
            if isinstance(dom, str) and dom and dom not in COMMON_EMAIL:
                by_email[dom].add(int(r.card1))
        for dom, cards in by_email.items():
            if len(cards) > EMAIL_HUB_CAP:
                continue
            for a in cards:
                peers = cards - {a}
                self._email_peers[a] |= peers
                self.adj[a] |= peers

    def prior_and_exposure(self, card1):
        rows = self.txn[self.txn.card1 == card1]
        if rows.empty:
            return 0.1, 0.0
        prior = float(rows.risk_score.max())
        exposure = float(rows.TransactionAmt.sum())
        return prior, exposure

    def shared_device_count(self, card1):
        return len(self._device_peers.get(int(card1), set()))

    def shared_email_count(self, card1):
        return len(self._email_peers.get(int(card1), set()))

    def velocity_24h(self, card1):
        dts = sorted(self.txn[self.txn.card1 == card1]["TransactionDT"].tolist())
        if len(dts) < 2:
            return len(dts)
        best, j = 1, 0
        for i in range(len(dts)):
            while dts[i] - dts[j] > DAY:
                j += 1
            best = max(best, i - j + 1)
        return best

    def identity_mismatch_score(self, card1):
        rows = self.txn[self.txn.card1 == card1]
        if rows.empty:
            return 0.0
        mcols = [f"M{i}" for i in range(1, 10)]
        # allow one incidental "F" per transaction before it counts as a mismatch,
        # so random noise doesn't flag benign cards
        avg_f = (rows[mcols] == "F").sum(axis=1).mean()
        f_score = max(0.0, avg_f - 1.0) / len(mcols)
        dist = rows["dist1"].fillna(0)
        dist_anom = 1.0 if float(dist.max()) > 500 else 0.0
        return round(min(1.0, f_score + 0.5 * dist_anom), 3)

    def hops_to_known_fraud(self, card1):
        fraud = self._known_fraud
        if card1 in fraud:
            return 0
        seen, q = {card1}, deque([(card1, 0)])
        while q:
            node, d = q.popleft()
            if d >= 4:
                continue
            for nb in self.adj.get(node, ()):
                if nb in fraud:
                    return d + 1
                if nb not in seen:
                    seen.add(nb)
                    q.append((nb, d + 1))
        return None

    # -- structural signature + cosine similarity to closed cases (vector-lite) --
    def _signature(self, card1):
        # small trailing bias (0.3) so two near-zero (benign) signatures still align
        # under cosine similarity - lets benign subjects match cleared past cases,
        # without swamping the discriminative structural dimensions.
        return [
            min(self.shared_device_count(card1) / 8.0, 1.0),
            min(self.shared_email_count(card1) / 6.0, 1.0),
            min(self.velocity_24h(card1) / 16.0, 1.0),
            self.identity_mismatch_score(card1),
            0.3,
        ]

    def similar_cases(self, card1, k=3):
        sig = self._signature(card1)

        def cos(a, b):
            dot = sum(x * y for x, y in zip(a, b))
            na = math.sqrt(sum(x * x for x in a)) or 1e-9
            nb = math.sqrt(sum(x * x for x in b)) or 1e-9
            return dot / (na * nb)

        scored = []
        for r in self.cases.itertuples():
            s = cos(sig, self._case_signatures[int(r.card1)])
            scored.append({"case_id": str(r.case_id), "similarity": round(float(s), 3),
                           "outcome": str(r.outcome), "typology": str(r.typology),
                           "action_taken": str(r.action_taken), "note": str(r.note)})
        scored.sort(key=lambda x: x["similarity"], reverse=True)
        return scored[:k]

    def ask_customer(self, card1):
        return "unauthorized" if card1 in self._true_fraud else "confirmed"

    def step_up_auth(self, card1):
        return "failed" if card1 in self._true_fraud else "passed"

    def write_case(self, case_dict):
        out = os.path.join(DATA, "..", "cases", f"{case_dict['case_id']}.written.json")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        import json
        with open(out, "w") as f:
            json.dump({"_note": "MOCK write-back (would be graph vertices in TigerGraph)",
                       **case_dict}, f, indent=2)


# ---------------------------------------------------------------------------
class TigerGraphClient(GraphClient):
    """
    Real backend. Each method runs an installed GSQL query (see graph/queries/).
    Fill in run_installed_query names once your instance + loading job are up.
    Vector similarity uses TigerGraph native vectorSearch (search_top_k_similarity via MCP).
    """
    def __init__(self):
        import pyTigerGraph as tg
        self.conn = tg.TigerGraphConnection(
            host=os.getenv("TG_HOST", "http://localhost"),
            graphname=os.getenv("TG_GRAPHNAME", "CLARA"),
            username=os.getenv("TG_USERNAME", "tigergraph"),
            password=os.getenv("TG_PASSWORD", "tigergraph"),
            restppPort=os.getenv("TG_RESTPP_PORT", "9000"),
            gsPort=os.getenv("TG_GSQL_PORT", "14240"),
        )
        secret = os.getenv("TG_SECRET")
        if secret:
            self.conn.getToken(secret)

    def _run(self, name, **params):
        return self.conn.runInstalledQuery(name, params)

    def prior_and_exposure(self, card1):
        r = self._run("ev_prior_exposure", card1=card1)[0]
        return float(r["prior"]), float(r["exposure"])

    def shared_device_count(self, card1):
        return int(self._run("ev_shared_device", card1=card1)[0]["peers"])

    def shared_email_count(self, card1):
        return int(self._run("ev_shared_email", card1=card1)[0]["peers"])

    def velocity_24h(self, card1):
        return int(self._run("ev_velocity", card1=card1)[0]["max_24h"])

    def identity_mismatch_score(self, card1):
        return float(self._run("ev_identity_mismatch", card1=card1)[0]["score"])

    def hops_to_known_fraud(self, card1):
        r = self._run("ev_path_to_fraud", card1=card1)[0]["hops"]
        return None if r is None or int(r) < 0 else int(r)

    def similar_cases(self, card1, k=3):
        # build the same structural signature the mock uses, then hybrid vector search
        sig = [
            min(self.shared_device_count(card1) / 8.0, 1.0),
            min(self.shared_email_count(card1) / 6.0, 1.0),
            min(self.velocity_24h(card1) / 16.0, 1.0),
            self.identity_mismatch_score(card1),
            0.3,
        ]
        return self._run("ev_similar_cases", sig=sig, k=k)

    def ask_customer(self, card1):
        return os.getenv("MOCK_CUSTOMER_RESPONSE", "unauthorized")  # stub API

    def step_up_auth(self, card1):
        return os.getenv("MOCK_STEPUP_RESPONSE", "failed")          # stub API

    def write_case(self, case_dict):
        # upsert a Case vertex + edges to implicated entities
        self._run("write_case", payload=__import__("json").dumps(case_dict))


def make_client() -> GraphClient:
    backend = os.getenv("CLARA_GRAPH_BACKEND", "mock").lower()
    if backend == "tigergraph":
        return TigerGraphClient()
    return MockGraphClient()
