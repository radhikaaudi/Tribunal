"""
Thin TigerGraph client for the investigator (pyTigerGraph 2.x, TG 4.x / Savanna).

    from clara import tg
    g = tg.get()            # singleton, or None when unconfigured / unreachable
    if g: g.device_neighbors(profile, "2016-11-01", "2016-12-31")

Never raises: every method returns an empty/False result on failure and records
the error in `last_error`. Every method call increments `calls` (tool_calls).

Env (.env): TG_HOST, TG_USERNAME, TG_PASSWORD, TG_SECRET, TG_GRAPHNAME (default DefAttack),
optional TG_RESTPP_PORT / TG_GS_PORT (default 443 for https hosts, 9000/14240 for http),
TG_TGCLOUD=true|false (default: true when host contains "tgcloud").
"""
from __future__ import annotations

import datetime as _dt
import os
import re
import threading
from typing import Any, Optional

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
except Exception:  # pragma: no cover
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QUERY_DIR = os.path.join(ROOT, "graph", "queries")
PING_TIMEOUT = float(os.getenv("TG_PING_TIMEOUT", "3"))
QUERY_TIMEOUT_MS = int(os.getenv("TG_QUERY_TIMEOUT_MS", "20000"))

_lock = threading.Lock()
_instance: Optional["TG"] = None
_failed: Optional[str] = None      # cached failure reason


def _cfg() -> Optional[dict]:
    host = (os.getenv("TG_HOST") or "").strip().rstrip("/")
    if not host:
        return None
    if not host.startswith("http"):
        host = "https://" + host
    https = host.startswith("https")
    cloud_env = os.getenv("TG_TGCLOUD")
    tg_cloud = (cloud_env.lower() == "true") if cloud_env else ("tgcloud" in host)
    default_rest, default_gs = ("443", "443") if https else ("9000", "14240")
    return dict(
        host=host,
        graphname=os.getenv("TG_GRAPHNAME") or "CLARA",
        username=os.getenv("TG_USERNAME") or "tigergraph",
        password=os.getenv("TG_PASSWORD") or "tigergraph",
        gsqlSecret=os.getenv("TG_SECRET") or "",
        # Savanna/tgcloud: everything is on 443 regardless of stale local-port env vars
        restppPort="443" if tg_cloud else (os.getenv("TG_RESTPP_PORT") or default_rest),
        gsPort="443" if tg_cloud else (os.getenv("TG_GS_PORT") or os.getenv("TG_GSQL_PORT") or default_gs),
        tgCloud=tg_cloud,
    )


def _ping(cfg: dict) -> bool:
    """Cheap reachability probe with a hard timeout (pyTigerGraph has none)."""
    import requests
    url = f"{cfg['host']}:{cfg['gsPort']}/api/ping"
    try:
        requests.get(url, timeout=PING_TIMEOUT)   # any HTTP answer (even 401) = reachable
        return True
    except Exception:
        return False


def connect(cfg: Optional[dict] = None, need_token: bool = True):
    """Return an authenticated pyTigerGraph connection or raise. Used by graph/load.py."""
    import pyTigerGraph as ptg
    cfg = cfg or _cfg()
    if cfg is None:
        raise RuntimeError("TG_HOST not set")
    if not _ping(cfg):
        raise RuntimeError(f"TigerGraph unreachable at {cfg['host']}:{cfg['gsPort']}")
    conn = ptg.TigerGraphConnection(**cfg)
    if need_token:
        try:
            if cfg["gsqlSecret"]:
                conn.getToken(cfg["gsqlSecret"])
            else:
                conn.getToken()
        except Exception as e:  # graph may not exist yet (load.py creates it) or auth is basic
            if "does not exist" not in str(e).lower():
                conn._token_error = str(e)
    return conn


def get() -> Optional["TG"]:
    """Singleton TG client, or None if unconfigured/unreachable (failure is cached)."""
    global _instance, _failed
    if _instance is not None:
        return _instance
    if _failed is not None:
        return None
    with _lock:
        if _instance is not None or _failed is not None:
            return _instance
        cfg = _cfg()
        if cfg is None:
            _failed = "unconfigured"
            return None
        try:
            conn = connect(cfg)
            conn.customizeHeader(timeout=QUERY_TIMEOUT_MS)
            _instance = TG(conn, cfg["graphname"])
        except Exception as e:
            _failed = f"{type(e).__name__}: {e}"
            return None
    return _instance


def failure_reason() -> Optional[str]:
    return _failed


def reset() -> None:
    """Forget the cached singleton/failure (e.g. after editing .env)."""
    global _instance, _failed
    _instance, _failed = None, None


def _ts(x) -> str:
    if x is None or x == "":
        return _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(x, (_dt.datetime, _dt.date)):
        return x.strftime("%Y-%m-%d %H:%M:%S")
    s = str(x).replace("T", " ")
    return s if len(s) > 10 else s + " 00:00:00"


def _rows(res: Any, key: str) -> list[dict]:
    """Flatten a PRINT <vertexset> AS key result into [{v_id, **attributes}]."""
    out = []
    for block in res or []:
        if isinstance(block, dict) and key in block:
            for v in block[key] or []:
                if isinstance(v, dict) and "attributes" in v:
                    out.append({"v_id": v.get("v_id"), **v["attributes"]})
                elif isinstance(v, dict):
                    out.append(v)
    return out


_PACK: Optional[dict] = None


def _case_pack() -> dict:
    """case_id -> {card_id, customer_id, flagged_txn_id} from dataset/case_pack.csv (cached)."""
    global _PACK
    if _PACK is None:
        _PACK = {}
        try:
            import csv
            with open(os.path.join(ROOT, "dataset", "case_pack.csv"), newline="") as f:
                for r in csv.DictReader(f):
                    _PACK[r["case_id"]] = r
        except Exception:
            pass
    return _PACK


class TG:
    def __init__(self, conn, graphname: str = "CLARA"):
        self.conn = conn
        self.graph = graphname
        self.calls = 0
        self.last_error: Optional[str] = None
        self._installed: Optional[set] = None
        self._policy_cache: Optional[list[dict]] = None

    # ------------------------------------------------------------------ plumbing
    def _installed_queries(self) -> set:
        if self._installed is None:
            try:
                qs = self.conn.getInstalledQueries() or {}
                self._installed = {k.rsplit("/", 1)[-1] for k in qs}
            except Exception:
                self._installed = set()
        return self._installed

    def query(self, name: str, params: dict) -> Optional[list]:
        """Run an installed query; fall back to INTERPRET QUERY from graph/queries/<name>.gsql."""
        self.calls += 1
        if name in self._installed_queries():
            try:
                return self.conn.runInstalledQuery(name, params, timeout=QUERY_TIMEOUT_MS)
            except Exception as e:
                self.last_error = f"{name}: {e}"
        try:
            with open(os.path.join(QUERY_DIR, f"{name}.gsql")) as f:
                text = "\n".join(l for l in f.read().splitlines() if not l.strip().startswith("//"))
            text = re.sub(r"CREATE\s+OR\s+REPLACE\s+QUERY\s+\w+\s*\(", "INTERPRET QUERY (", text, count=1)
            text = text.replace("FOR GRAPH CLARA", f"FOR GRAPH {self.graph}")
            return self.conn.runInterpretedQuery(text, params)
        except Exception as e:
            self.last_error = f"{name} (interpreted): {e}"
            return None

    # ------------------------------------------------------------------ reads
    def device_neighbors(self, profile: str, t0, t1) -> list[dict]:
        """Transactions on `profile` in [t0, t1]: [{v_id, customer_id, card_id, ts, amount, ...}]."""
        if not profile:
            return []
        res = self.query("device_neighbors", {"profile": (profile,), "t0": _ts(t0), "t1": _ts(t1)})
        return _rows(res, "txns")

    def customer_history(self, customer_id: str) -> list[dict]:
        res = self.query("customer_history", {"customer": (customer_id,)})
        return sorted(_rows(res, "txns"), key=lambda r: str(r.get("ts", "")))

    def prior_cases_for_customer(self, customer_id: str) -> dict:
        """{"closed_cases": [...], "investigations": [...]}"""
        res = self.query("prior_cases_for_customer", {"customer": (customer_id,)})
        return {"closed_cases": _rows(res, "closed_cases"), "investigations": _rows(res, "investigations")}

    def link_to_known_fraud(self, customer_id: str, max_device_customers: int = 150) -> dict:
        """{"devices": [...], "closed_cases": [... @via_device, @via_txn, @via_card], "investigations": [...]}"""
        res = self.query("link_to_known_fraud",
                         {"customer": (customer_id,), "max_device_customers": max_device_customers})
        return {"devices": _rows(res, "devices"), "closed_cases": _rows(res, "closed_cases"),
                "investigations": _rows(res, "investigations")}

    def _policy_docs(self) -> list[dict]:
        if self._policy_cache is None:
            try:
                vs = self.conn.getVertices("PolicyDoc", limit=5000)
                self._policy_cache = [{"id": v["v_id"], **v["attributes"]} for v in vs]
            except Exception as e:
                self.last_error = f"policy docs: {e}"
                return []
        return self._policy_cache

    def policy_search(self, text: str, k: int = 3) -> list[dict]:
        """Keyword-scored retrieval over PolicyDoc chunks -> [{"id","section","text"}]."""
        self.calls += 1
        docs = self._policy_docs()
        if not docs:
            return []
        q = str(text or "").lower()
        rule_ids = set(re.findall(r"\br\d+\b|\b3[ab]\b", q))
        terms = [w for w in re.findall(r"[a-z0-9_]+", q) if len(w) > 2]
        scored = []
        for d in docs:
            hay = (d.get("section", "") + " " + d.get("text", "")).lower()
            s = sum(hay.count(w) for w in terms)
            if d.get("section", "").lower() in rule_ids or d["id"].lower() in rule_ids:
                s += 100
            if q and q in hay:
                s += 20
            if s:
                scored.append((s, d))
        scored.sort(key=lambda x: -x[0])
        return [{"id": d["id"], "section": d.get("section", ""), "text": d.get("text", "")}
                for _, d in scored[:k]]

    def similar_investigations(self, pattern: str, k: int = 5) -> list[dict]:
        """Previously written InvestigationCase vertices (case memory) with this pattern."""
        self.calls += 1
        try:
            p = str(pattern).replace('"', "")
            vs = self.conn.getVertices("InvestigationCase", where=f'pattern="{p}"', limit=500)
            rows = [{"id": v["v_id"], **v["attributes"]} for v in vs]
            rows.sort(key=lambda r: str(r.get("written_at", "")), reverse=True)
            return rows[:k]
        except Exception as e:
            self.last_error = f"similar_investigations: {e}"
            return []

    # ------------------------------------------------------------------ write-back
    def write_case(self, answer: dict) -> tuple[bool, str]:
        """Upsert the agent's case as an InvestigationCase vertex + edges.
        Returns (True, vertex_id) only if the vertex upsert was accepted."""
        self.calls += 1
        try:
            case_id = str(answer.get("case_id", ""))
            c = answer.get("case", {}) or {}
            vid = c.get("graph_case_id") or f"CASE-{case_id}"
            card_id = str(answer.get("card_id") or c.get("card_id") or "")
            customer_id = str(answer.get("customer_id") or c.get("customer_id") or "")
            pack = _case_pack().get(case_id, {})
            card_id = card_id or str(pack.get("card_id", ""))
            customer_id = customer_id or str(pack.get("customer_id", ""))
            answer = {**answer, "flagged_txn_id": answer.get("flagged_txn_id") or pack.get("flagged_txn_id")}
            if not card_id and customer_id:
                card_id = f"{customer_id}-K1"
            if card_id and not customer_id:
                customer_id = card_id.split("-")[0]
            finals = (answer.get("next_best_actions", {}) or {}).get("final", []) or []
            actions = "|".join(f"{a.get('action')}:{a.get('route')}" for a in finals if isinstance(a, dict))
            attrs = {
                "case_id": case_id, "customer_id": customer_id, "card_id": card_id,
                "status": str(c.get("status", "")), "verdict": str(c.get("verdict", "")),
                "fraud_probability": float(c.get("fraud_probability") or 0.0),
                "pattern": str(c.get("pattern", "")), "exposure": float(c.get("exposure_usd") or 0.0),
                "summary": str(c.get("summary", ""))[:4000], "actions": actions,
                "sar_file": bool((answer.get("sar", {}) or {}).get("file", False)),
                "written_at": _ts(None),
            }
            n = self.conn.upsertVertex("InvestigationCase", vid, attrs)
            if not n:
                self.last_error = "write_case: upsertVertex accepted 0 vertices"
                return False, ""
            # edges: best-effort; vertexMustExist so we never create phantom txns/devices/cases
            if card_id:
                self.conn.upsertEdges("InvestigationCase", "CASE_ON_CARD", "Card", [(vid, card_id, {})])
            txns = [str(t) for t in c.get("affected_txn_ids", []) or []]
            flagged = answer.get("flagged_txn_id")
            if flagged:
                txns.append(str(flagged))
            if txns:
                self.conn.upsertEdges("InvestigationCase", "CASE_INVOLVES", "Transaction",
                                      [(vid, t, {}) for t in dict.fromkeys(txns)], vertexMustExist=True)
            devs = [d for d in c.get("connected_device_profiles", []) or [] if d]
            if devs:
                self.conn.upsertEdges("InvestigationCase", "CASE_DEVICE", "DeviceProfile",
                                      [(vid, d, {}) for d in devs], vertexMustExist=True)
            sims = [s for s in c.get("similar_prior_cases", []) or [] if s]
            if sims:
                self.conn.upsertEdges("InvestigationCase", "CASE_SIMILAR_TO", "ClosedCase",
                                      [(vid, s, {}) for s in sims], vertexMustExist=True)
            return True, vid
        except Exception as e:
            self.last_error = f"write_case: {e}"
            return False, ""

    def ping(self) -> bool:
        try:
            self.conn.echo()
            return True
        except Exception:
            return False
