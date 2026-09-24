"""
Tribunal analyst console (real HHGOA dataset).

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

from tribunal.investigator import investigate
from tribunal.realdata import load

load_dotenv()
st.set_page_config(page_title="Tribunal — Fraud Investigation Agent", page_icon="🕵️", layout="wide")


@st.cache_resource
def dataset():
    return load()


st.markdown("## 🕵️ Tribunal — Confidence-Led Adaptive Risk Agent")
st.caption("Investigates a fraud alert on TigerGraph · gathers evidence until the decision is "
           "settled · recommends a policy-bound next-best-action")

ds = dataset()
CLEAR, BLOCK = 0.15, 0.85

with st.sidebar:
    st.subheader("Case pack (20 exam cases)")
    row = st.selectbox("Case", ds.case_pack.itertuples(),
                       format_func=lambda r: f"{r.case_id} · {r.trigger_type}")
    st.write(f"**Card** {row.card_id}  ·  **Customer** {row.customer_id}")
    st.caption(row.trigger_text)
    speed = st.slider("Animation delay (s)", 0.0, 1.2, 0.5, 0.1)
    go = st.button("▶ Investigate", type="primary", use_container_width=True)


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

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Verdict", c["verdict"])
    m2.metric("Confidence", f"{c['fraud_probability']:.0%}")
    m3.metric("Pattern", c["pattern"])
    m4.metric("Exposure", f"${c['exposure_usd']:,.0f}")

    st.info(f"🛑 **Stop:** {ans['stop_reason']}")

    left, right = st.columns([3, 2])
    with left:
        st.markdown("#### Evidence")
        st.dataframe(pd.DataFrame([{"source": e["source"], "claim": e["claim"]}
                                   for e in c["evidence"]]), hide_index=True, use_container_width=True)
        st.markdown("#### Next-best-action")
        a, b = st.columns(2)
        a.markdown("**Initial** (before evidence request)")
        a.table(pd.DataFrame(ans["next_best_actions"]["initial"]))
        b.markdown("**Final** (after)")
        b.table(pd.DataFrame(ans["next_best_actions"]["final"]))
        st.caption("What changed: " + ans["next_best_actions"]["what_changed"])
    with right:
        st.markdown("#### Case memory (similar prior cases)")
        sims = ds.closed[ds.closed.case_id.isin(c["similar_prior_cases"])]
        st.dataframe(sims[["case_id", "pattern", "outcome", "exposure_usd"]] if len(sims)
                     else pd.DataFrame(), hide_index=True, use_container_width=True)
        if c["connected_card_ids"]:
            st.markdown(f"#### Connected cards ({len(c['connected_card_ids'])})")
            st.caption(", ".join(c["connected_card_ids"][:20]))

    if ans["sar"]["file"]:
        with st.expander("📄 Suspicious Activity Report (auto-draft)", expanded=True):
            st.write(ans["sar"]["narrative"])
            st.json({k: ans["sar"][k] for k in ("reason", "subjects", "total_amount_usd", "activity_dates")})

    st.markdown("#### Case summary"); st.write(c["summary"])
    with st.expander("Full answer JSON (submission format)"):
        st.json(ans)
else:
    st.info("Pick a case and press **Investigate**. Try **HHG-014** (analyst-flagged device ring → "
            "undocumented pattern + SAR), **HHG-010** (risk alert → VERIFY then BLOCK as evidence arrives), "
            "and **HHG-012** (high score but benign → cleared).")
