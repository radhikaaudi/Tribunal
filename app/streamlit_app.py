"""
DefAttack analyst console (real HHGOA dataset).

  streamlit run app/streamlit_app.py

Pick one of the 20 case-pack cases; watch the belief move as evidence arrives, see the
pattern, the initial-vs-final next-best-action with approval routes, the retrieved
prior cases, and the SAR. This is the demo surface.

Prereq: python data/build_cache.py
"""
import os
import sys
import time

import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
import pandas as pd

from clara.investigator import investigate
from clara.realdata import load

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT, ".env"))
st.set_page_config(page_title="DefAttack — Fraud Investigation Agent", page_icon="🛡️", layout="wide")


@st.cache_resource
def dataset():
    return load()


@st.cache_data(ttl=60)
def graph_status():
    """Live TigerGraph (Savanna) + MCP status for the sidebar."""
    out = {"tg": False, "mcp": False}
    try:
        from clara import tg
        g = tg.get()
        if g is not None:
            c = g.conn
            out.update(tg=True, host=c.host.replace("https://", "").split(".")[0], graph=c.graphname)
            vt = ["Transaction", "Card", "Customer", "DeviceProfile", "ClosedCase", "InvestigationCase", "PolicyDoc"]
            out["vertices"] = {v: c.getVertexCount(v) for v in vt}
            out["edges"] = c.getEdgeCount("*") if hasattr(c, "getEdgeCount") else None
            inv = c.getVertices("InvestigationCase", limit=100) or []
            out["cases"] = sorted(v["v_id"] for v in inv)
        else:
            out["why"] = tg.failure_reason()
    except Exception as e:
        out["why"] = str(e)[:120]
    try:
        from clara import mcp_tools
        m = mcp_tools.get()
        out["mcp"] = m is not None
        out["mcp_tools"] = len(m.list_tools()) if m else 0
    except Exception:
        pass
    return out


st.markdown("## 🛡️ DefAttack — Agentic Fraud Investigation")
st.caption("Investigates a fraud alert on TigerGraph · gathers evidence until the decision is "
           "settled · recommends a policy-bound next-best-action")

ds = dataset()
CLEAR, BLOCK = 0.15, 0.85


def md(text) -> str:
    """Streamlit renders $...$ as LaTeX; escape dollar amounts."""
    return str(text).replace("$", "\\$")

with st.sidebar:
    st.subheader("Case pack (20 exam cases)")
    row = st.selectbox("Case", ds.case_pack.itertuples(),
                       format_func=lambda r: f"{r.case_id} · {r.trigger_type}")
    st.write(f"**Card** {row.card_id}  ·  **Customer** {row.customer_id}")
    st.caption(md(row.trigger_text))
    speed = st.slider("Animation delay (s)", 0.0, 1.2, 0.5, 0.1)
    use_llm = st.checkbox("Write summary & SAR with the LLM (slower, free-tier rate limits)", value=False)
    go = st.button("▶ Investigate", type="primary", width='stretch')

    st.divider()
    st.subheader("TigerGraph")
    gs = graph_status()
    if gs["tg"]:
        st.success(f"Connected · Savanna `{gs['host'][:14]}…` · graph **{gs['graph']}**")
        v = gs["vertices"]
        a, b = st.columns(2)
        a.metric("Transactions", f"{v['Transaction']:,}")
        b.metric("Closed cases", f"{v['ClosedCase']:,}")
        a.metric("Device profiles", f"{v['DeviceProfile']:,}")
        b.metric("Cases written", v["InvestigationCase"])
        if isinstance(gs.get("edges"), dict):
            st.caption(f"{sum(gs['edges'].values()):,} edges · {v['PolicyDoc']} policy docs (GraphRAG)")
    else:
        st.warning(f"Not connected — running on the offline pandas graph. {gs.get('why') or ''}")
    st.caption(("🟢 TigerGraph MCP server: " + f"{gs.get('mcp_tools', 0)} tools") if gs["mcp"]
               else "⚪ TigerGraph MCP server: not running")
    import importlib
    from clara import llm as _llm
    _llm = importlib.reload(_llm)   # pick up code changes without restarting streamlit
    prov = _llm.provider()
    st.caption(f"🟢 LLM narration: {prov.capitalize()}" if prov != "none" else "⚪ LLM narration: off (template text)")


def meter(ph, prob):
    color = "#16a34a" if prob <= CLEAR else "#dc2626" if prob >= BLOCK else "#d97706"
    label = "CLEAR" if prob <= CLEAR else "BLOCK" if prob >= BLOCK else "uncertain"
    ph.markdown(
        f"""<div style="border:1px solid #333;border-radius:10px;padding:12px 14px;">
          <div style="display:flex;justify-content:space-between;font-size:12px;color:#888;">
            <span>clear ≤{CLEAR:.0%}</span><span>{label}</span><span>block ≥{BLOCK:.0%}</span></div>
          <div style="background:#222;border-radius:8px;height:32px;position:relative;margin-top:6px;">
            <div style="width:{prob*100:.1f}%;background:{color};height:32px;border-radius:8px;transition:width .4s;"></div>
            <div style="position:absolute;top:5px;left:12px;font-weight:700;font-size:17px;color:#fff;">
              fraud confidence {prob:.0%}</div></div></div>""", unsafe_allow_html=True)


if go:
    st.markdown("#### Belief")
    meter_ph = st.empty()
    traj_ph = st.empty()
    traj = []

    def on_step(label, prob):
        traj.append({"step": label, "confidence": round(prob, 3)})
        meter(meter_ph, prob)
        traj_ph.line_chart(pd.DataFrame(traj).set_index("step"), height=160)
        time.sleep(speed)

    meter(meter_ph, 0.25)
    import importlib
    import clara.investigator as _inv
    _inv = importlib.reload(_inv)
    with st.spinner("Writing the summary and SAR with the LLM…" if use_llm else "Investigating…"):
        ans = _inv.investigate(ds, row._asdict(), on_step=on_step, use_llm=use_llm)
    c = ans["case"]
    meter(meter_ph, c["fraud_probability"])

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Verdict", c["verdict"])
    m2.metric("Confidence", f"{c['fraud_probability']:.0%}")
    m3.metric("Pattern", c["pattern"])
    m4.metric("Exposure", f"${c['exposure_usd']:,.0f}")

    st.info(md(f"🛑 **Stop:** {ans['stop_reason']}"))
    g1, g2, g3, g4 = st.columns(4)
    g1.metric("Status", c["status"])
    g2.metric("Written to TigerGraph", "yes" if c["written_to_graph"] else "no",
              c["graph_case_id"] or None)
    g3.metric("Tool calls", ans["tool_calls"])
    g4.metric("LLM tokens", ans["tokens"])

    d = ans.get("debate", {})
    if d:
        st.markdown("#### Tribunal: prosecution vs defence")
        pc, dc = st.columns(2)
        pc.markdown(md("**Prosecution (points to fraud)**\n" + "\n".join(f"- {x}" for x in d["prosecution"])))
        dc.markdown(md("**Defence (legitimate explanations)**\n" + ("\n".join(f"- {x}" for x in d["defense"]) or "- none found")))
        st.caption(md("Conclusion: " + d["conclusion"]))

    if ans["evidence_requests"]:
        st.markdown("#### Evidence requested (simulated response, policy §5)")
        st.dataframe(pd.DataFrame(ans["evidence_requests"]), hide_index=True, width='stretch')

    left, right = st.columns([3, 2])
    with left:
        st.markdown("#### Evidence")
        st.dataframe(pd.DataFrame([{"source": e["source"], "claim": e["claim"], "ref": e["ref"],
                                    "entities": ", ".join(e["entity_ids"][:4])}
                                   for e in c["evidence"]]), hide_index=True, width='stretch')
        st.markdown("#### Next-best-action")
        a, b = st.columns(2)
        a.markdown("**Initial** (before evidence request)")
        a.table(pd.DataFrame(ans["next_best_actions"]["initial"]))
        b.markdown("**Final** (after)")
        b.table(pd.DataFrame(ans["next_best_actions"]["final"]))
        st.caption(md("What changed: " + ans["next_best_actions"]["what_changed"]))
    with right:
        st.markdown("#### Case memory (similar prior cases)")
        sims = ds.closed[ds.closed.case_id.isin(c["similar_prior_cases"])]
        st.dataframe(sims[["case_id", "pattern", "outcome", "exposure_usd", "analyst_notes"]] if len(sims)
                     else pd.DataFrame(), hide_index=True, width='stretch')
        if c["connected_card_ids"]:
            st.markdown(f"#### Connected cards ({len(c['connected_card_ids'])})")
            st.caption(", ".join(c["connected_card_ids"][:20]))

    if c["pattern_description"]:
        st.warning(md("**Undocumented pattern:** " + c["pattern_description"]))
    if ans["sar"]["file"]:
        with st.expander("📄 Suspicious Activity Report (auto-draft)", expanded=True):
            st.write(md(ans["sar"]["narrative"]))
            st.json({k: ans["sar"][k] for k in ("reason", "subjects", "total_amount_usd", "activity_dates")})

    st.markdown("#### Case summary"); st.write(md(c["summary"]))
    with st.expander("Full answer JSON (submission format)"):
        st.json(ans)
else:
    import glob, json
    rows = []
    for fn in sorted(glob.glob(os.path.join(ROOT, "cases", "HHG-*.json"))):
        a = json.load(open(fn)); c = a["case"]
        rows.append({"case": a["case_id"], "verdict": c["verdict"], "p(fraud)": c["fraud_probability"],
                     "pattern": c["pattern"], "exposure $": c["exposure_usd"], "status": c["status"],
                     "final actions": ", ".join(x["action"] for x in a["next_best_actions"]["final"]),
                     "SAR": "yes" if a["sar"]["file"] else "", "in TigerGraph": c["graph_case_id"] or "no"})
    if rows:
        df = pd.DataFrame(rows)
        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("Cases", len(df)); m2.metric("Fraud", int((df.verdict == "fraud").sum()))
        m3.metric("Legitimate", int((df.verdict == "legitimate").sum()))
        m4.metric("Uncertain", int((df.verdict == "uncertain").sum())); m5.metric("SARs", int((df.SAR == "yes").sum()))
        st.markdown("#### Case pack results (answer files in `cases/`)")
        st.dataframe(df, hide_index=True, width='stretch')
    st.info("Pick a case and press **Investigate**. Try **HHG-014** (analyst-flagged device ring → "
            "undocumented pattern + SAR), **HHG-010** (\\$1,000 alert → step-up, no reply → uncertain, R4 + R8 escalation), "
            "**HHG-006** (four purchases just under \\$500 → undocumented structuring + SAR), "
            "and **HHG-012** (score 0.55, customer confirms → cleared).")
