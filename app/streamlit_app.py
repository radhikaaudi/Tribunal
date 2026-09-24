"""
Tribunal analyst console (real HHGOA dataset).

  streamlit run app/streamlit_app.py

Pick one of the 20 case-pack cases and watch the whole agentic investigation:
the belief move as evidence arrives, the fraud pattern, the LLM's grounded reasoning
and tool selection, the GraphRAG context it was grounded on, the case progression
timeline, the case memory it retrieved, the initial-vs-final next-best-action with
approval routes, and the SAR. This is the demo surface.

Prereq: python data/build_cache.py
"""
import os
import sys
import time

import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
import pandas as pd

from tribunal.investigator import investigate
from tribunal.realdata import load

load_dotenv()
st.set_page_config(page_title="Tribunal — Fraud Investigation Agent", page_icon="⚖️", layout="wide")


@st.cache_resource
def dataset():
    return load()


st.markdown("## ⚖️ Tribunal — Agentic Fraud Investigation")
st.caption("Investigates a fraud alert on a TigerGraph knowledge graph · gathers evidence until the "
           "decision is settled · grounds every recommendation in policy (GraphRAG) · recommends a "
           "policy-bound next-best-action with the right approval route")

ds = dataset()
CLEAR, BLOCK = 0.15, 0.85

with st.sidebar:
    st.subheader("Case pack (20 exam cases)")
    row = st.selectbox("Case", ds.case_pack.itertuples(),
                       format_func=lambda r: f"{r.case_id} · {r.trigger_type}")
    st.write(f"**Card** {row.card_id}  ·  **Customer** {row.customer_id}")
    st.caption(row.trigger_text)
    speed = st.slider("Animation delay (s)", 0.0, 1.2, 0.4, 0.1)
    go = st.button("▶ Investigate", type="primary", use_container_width=True)
    st.divider()
    st.caption(f"Graph backend: `{os.getenv('TRIBUNAL_GRAPH_BACKEND', 'mock')}`  ·  "
               f"LLM: `{os.getenv('TRIBUNAL_LLM_PROVIDER', 'none')}`")


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
    ans = investigate(ds, row._asdict(), on_step=on_step)
    c = ans["case"]
    meter(meter_ph, c["fraud_probability"])

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Verdict", c["verdict"])
    m2.metric("Confidence", f"{c['fraud_probability']:.0%}")
    m3.metric("Pattern", c["pattern"])
    m4.metric("Exposure", f"${c['exposure_usd']:,.0f}")
    m5.metric("Tool calls", ans["tool_calls"])
    st.info(f"🛑 **Stop:** {ans['stop_reason']}")

    tabs = st.tabs(["🧠 Reasoning", "📚 GraphRAG", "🗂 Case progression", "🧩 Memory",
                    "🔎 Evidence", "🎯 Next-best-action", "📄 SAR", "{ } JSON"])

    # ---- Reasoning (LLM: synthesis + tool selection, grounded) ----
    with tabs[0]:
        r = ans["agent_reasoning"]
        st.markdown(f"**Agent reasoning**  ·  LLM provider: `{r['provider']}`")
        st.write(r["reasoning"])
        st.warning(f"**Uncertainty:** {r['uncertainty']}")
        st.markdown("**Tool selection** (which graph tools the agent ran and why)")
        st.dataframe(pd.DataFrame(r["tool_plan"]), hide_index=True, use_container_width=True)
        st.caption("Grounded on: " + " · ".join(r.get("grounded_on", [])))
        d = ans["debate"]
        cc1, cc2 = st.columns(2)
        cc1.markdown("**Prosecution**"); cc1.write("\n".join(f"- {x}" for x in d["prosecution"]) or "—")
        cc2.markdown("**Defence**"); cc2.write("\n".join(f"- {x}" for x in d["defense"]) or "—")
        st.success("**Conclusion:** " + d["conclusion"])

    # ---- GraphRAG grounding ----
    with tabs[1]:
        g = ans["graphrag"]
        st.markdown(f"**Retrieval backend:** `{g['backend']}`  ·  query: _{g['query']}_")
        st.markdown("**Fraud policy (governing rules)**")
        for d in g["policy"]:
            st.markdown(f"> `{d['citation']}`  (score {d['score']})\n>\n> {d['text'][:400]}")
        st.markdown("**Known typologies**")
        for d in g["typology"]:
            st.markdown(f"> `{d['citation']}`  (score {d['score']})\n>\n> {d['text'][:300]}")
        st.markdown("**Regulatory references**")
        for d in g["regulatory"]:
            st.markdown(f"> `{d['citation']}`  (score {d['score']})\n>\n> {d['text'][:300]}")
        st.markdown("**Connected graph evidence**")
        st.code("\n".join(g["graph_context"]), language="text")

    # ---- Case progression ----
    with tabs[2]:
        p = ans["case_progression"]
        st.markdown(f"**Case {p['case_id']}**  ·  opened `{p['opened_at']}`  ·  "
                    f"status **{p['status']}**  ·  {p['n_events']} events")
        st.markdown("**Status history:** " + " → ".join(p["status_history"]))
        st.dataframe(pd.DataFrame(p["timeline"]), hide_index=True, use_container_width=True)

    # ---- Memory ----
    with tabs[3]:
        mem = ans["case_memory"]
        st.markdown(f"**Case memory** — {mem['memory_size']} cases in the store "
                    f"(closed cases + resolved investigations, grows as cases close)")
        st.markdown("**Retrieved similar prior cases** (vector retrieval on the case signature)")
        st.dataframe(pd.DataFrame(mem["retrieved"]), hide_index=True, use_container_width=True)
        rec = mem["recurring_entities"]
        cc1, cc2 = st.columns(2)
        cc1.markdown("**Recurring device profiles**")
        cc1.dataframe(pd.DataFrame(rec["device_profiles"]) if rec["device_profiles"] else pd.DataFrame(),
                      hide_index=True, use_container_width=True)
        cc2.markdown("**Recurring connected cards**")
        cc2.dataframe(pd.DataFrame(rec["connected_cards"]) if rec["connected_cards"] else pd.DataFrame(),
                      hide_index=True, use_container_width=True)

    # ---- Evidence ----
    with tabs[4]:
        st.dataframe(pd.DataFrame([{"source": e["source"], "side": e.get("side", ""),
                                    "claim": e["claim"], "ref": e.get("ref", "")}
                                   for e in c["evidence"]]), hide_index=True, use_container_width=True)
        if c["connected_card_ids"]:
            st.markdown(f"**Connected cards ({len(c['connected_card_ids'])})**")
            st.caption(", ".join(c["connected_card_ids"][:20]))

    # ---- NBA ----
    with tabs[5]:
        a, b = st.columns(2)
        a.markdown("**Initial** (before evidence request)")
        a.table(pd.DataFrame(ans["next_best_actions"]["initial"]))
        b.markdown("**Final** (after)")
        b.table(pd.DataFrame(ans["next_best_actions"]["final"]))
        st.caption("What changed: " + ans["next_best_actions"]["what_changed"])
        if ans["evidence_requests"]:
            st.markdown("**Evidence requested** (controlled, policy-approved)")
            st.table(pd.DataFrame(ans["evidence_requests"]))

    # ---- SAR ----
    with tabs[6]:
        if ans["sar"]["file"]:
            st.write(ans["sar"]["narrative"])
            st.json({k: ans["sar"][k] for k in ("reason", "subjects", "total_amount_usd", "activity_dates")})
        else:
            st.info(ans["sar"]["reason"])

    # ---- JSON ----
    with tabs[7]:
        st.caption("Exact submission format written to cases/" + ans["case_id"] + ".json")
        st.json(ans)

    st.markdown("#### Case summary"); st.write(c["summary"])
else:
    st.info("Pick a case and press **Investigate**. Try **HHG-014** (analyst-flagged device ring → "
            "undocumented pattern + SAR), **HHG-010** (risk alert → VERIFY then BLOCK as evidence arrives), "
            "and **HHG-012** (high score but benign → cleared).")
