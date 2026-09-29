"""
dashboard/app.py

OpsPilot — Streamlit dashboard

Run from the project root:
    streamlit run dashboard/app.py

Reads from the same DB Phase 4 built (SQLite dev at data/opspilot.db, or
DATABASE_URL for Postgres in prod — no code change needed to switch, same
as Phase 4). See dashboard/data_access.py for why it reads via introspection
rather than importing your ORM models directly, and dashboard/metrics.py for
the Risk Score / Confidence formulas.

"""

import streamlit as st
import pandas as pd
import plotly.express as px

from data_access import (
    load_complaints_full,
    load_patterns_with_recommendations,
    load_executive_summary,
    introspect,
)
from metrics import compute_risk_score, confidence_health

st.set_page_config(page_title="OpsPilot", layout="wide", page_icon="◈")

st.markdown(
    """
    <style>
      .block-container { max-width: 1440px; padding-top: 2.5rem; padding-bottom: 2rem; }
      [data-testid="stMetric"] { background: #f7f8fa; border: 1px solid #e7e9ee; border-radius: 10px; padding: 0.85rem; }
      h1, h2, h3 { letter-spacing: -0.02em; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Caching: data is queried once every 5 minutes, or immediately on manual
# refresh. Chosen over live-query-every-interaction because every filter
# tweak in the sidebar would otherwise re-hit SQLite on each rerun — fine at
# today's row counts, but this scales cleanly once Phase 6 real-time
# ingestion lands and the DB grows.
# ---------------------------------------------------------------------------
@st.cache_data(ttl=300)
def get_data():
    return {
        "complaints": load_complaints_full(),
        "patterns": load_patterns_with_recommendations(),
        "exec_summary": load_executive_summary(),
    }


with st.sidebar:
    st.title("OpsPilot")
    st.caption("Operations intelligence")

    if st.button("🔄 Refresh data"):
        st.cache_data.clear()
        st.rerun()

    st.divider()

    with st.expander("System status", expanded=False):
        report = introspect()
        for table, info in report.items():
            if not info.get("exists"):
                st.markdown(f"- {table}: unavailable")
            elif info["missing_expected"]:
                st.markdown(f"- {table}: partial")
            else:
                st.markdown(f"- {table}: available")

data = get_data()
df = data["complaints"]

if df.empty:
    st.error(
        "No data found. Check that data/opspilot.db exists and Phase 4's "
        "`python -m data.load_outputs` has been run, or set DATABASE_URL if "
        "you're pointing at Postgres."
    )
    st.stop()

# ---------------------------------------------------------------------------
# Sidebar filters — built only from columns that actually exist
# ---------------------------------------------------------------------------
with st.sidebar:
    st.divider()
    st.subheader("Filters")
    filtered = df.copy()

    if "date" in df.columns and df["date"].notna().any():
        min_d, max_d = df["date"].min(), df["date"].max()
        date_range = st.date_input("Date range", value=(min_d.date(), max_d.date()))
        if isinstance(date_range, tuple) and len(date_range) == 2:
            start, end = date_range
            filtered = filtered[
                (filtered["date"].dt.date >= start) & (filtered["date"].dt.date <= end)
            ]

    if "store" in df.columns:
        stores = sorted(df["store"].dropna().unique().tolist())
        picked = st.multiselect("Store", stores)
        if picked:
            filtered = filtered[filtered["store"].isin(picked)]

    if "source" in df.columns:
        sources = sorted(df["source"].dropna().unique().tolist())
        picked_src = st.multiselect("Source", sources)
        if picked_src:
            filtered = filtered[filtered["source"].isin(picked_src)]

    if "category" in df.columns:
        cats = sorted(df["category"].dropna().unique().tolist())
        picked_cat = st.multiselect("Category", cats)
        if picked_cat:
            filtered = filtered[filtered["category"].isin(picked_cat)]

    st.divider()
    show_ai_detail = st.toggle("Show AI-generated analysis", value=False)

filtered_classified = filtered.dropna(subset=["category"]) if "category" in filtered.columns else pd.DataFrame()

# ---------------------------------------------------------------------------
# KPI row
# ---------------------------------------------------------------------------
st.title("Operations overview")
st.caption("A concise view of recorded complaint activity and operational signals.")

risk = compute_risk_score(filtered_classified)
conf = confidence_health(filtered_classified)

k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Recorded complaints", len(filtered))
k2.metric("Classification coverage", f"{len(filtered_classified) / len(filtered):.0%}" if len(filtered) else "—")
k3.metric(
    "Risk indicator",
    f"{risk['score']}" if risk["score"] is not None else "—",
    help=risk.get("note", ""),
)
k4.metric(
    "Classification confidence",
    f"{conf['mean']:.0%}" if conf["mean"] is not None else "—",
    help=f"{conf.get('low_confidence_count', 0)} complaint(s) below "
         f"{conf.get('low_thresh', 0.7):.0%} confidence" if conf["mean"] is not None else "",
)
k5.metric(
    "Locations represented",
    filtered["store"].nunique() if "store" in filtered.columns else "—",
)

st.divider()

# ---------------------------------------------------------------------------
# Volume over time + Category breakdown
# ---------------------------------------------------------------------------
c1, c2 = st.columns([2, 1])

with c1:
    st.subheader("Complaint Volume Over Time")
    if "date" in filtered.columns and filtered["date"].notna().any():
        vol = filtered.dropna(subset=["date"]).groupby(filtered["date"].dt.to_period("W")).size()
        vol.index = vol.index.astype(str)
        fig = px.bar(x=vol.index, y=vol.values, labels={"x": "Week", "y": "Complaints"})
        fig.update_layout(template="simple_white", margin=dict(l=0, r=0, t=10, b=0))
        st.plotly_chart(fig, width="stretch")
    else:
        st.info("No usable `date` column found.")

with c2:
    st.subheader("By Category")
    if not filtered_classified.empty and "category" in filtered_classified.columns:
        cat_counts = filtered_classified["category"].value_counts()
        fig = px.pie(values=cat_counts.values, names=cat_counts.index, hole=0.4)
        fig.update_layout(template="simple_white", margin=dict(l=0, r=0, t=10, b=0))
        st.plotly_chart(fig, width="stretch")
    else:
        st.info("No classified complaints in this filter yet.")

# ---------------------------------------------------------------------------
# Sentiment trend + Root cause distribution
# ---------------------------------------------------------------------------
c3, c4 = st.columns(2)

with c3:
    st.subheader("Sentiment Trend")
    if not filtered_classified.empty and {"sentiment", "date"}.issubset(filtered_classified.columns):
        sent = filtered_classified.dropna(subset=["date"]).copy()
        sent["week"] = sent["date"].dt.to_period("W").astype(str)
        pivot = sent.groupby(["week", "sentiment"]).size().unstack(fill_value=0)
        fig = px.line(pivot, x=pivot.index, y=pivot.columns, markers=True,
                       labels={"value": "Complaints", "week": "Week"})
        fig.update_layout(template="simple_white", margin=dict(l=0, r=0, t=10, b=0))
        st.plotly_chart(fig, width="stretch")
    else:
        st.info("Need `sentiment` + `date` on classified rows to plot this.")

with c4:
    st.subheader("Root Cause Distribution")
    if "root_cause" in filtered.columns and filtered["root_cause"].notna().any():
        rc_counts = filtered["root_cause"].dropna().value_counts().head(10)
        fig = px.bar(x=rc_counts.values, y=rc_counts.index, orientation="h",
                      labels={"x": "Complaints", "y": ""})
        fig.update_layout(yaxis={"categoryorder": "total ascending"})
        fig.update_layout(template="simple_white", margin=dict(l=0, r=0, t=10, b=0))
        st.plotly_chart(fig, width="stretch")
    else:
        st.info("No root-caused complaints in this filter yet (Phase 3 output).")

st.divider()

# ---------------------------------------------------------------------------
# Store performance
# ---------------------------------------------------------------------------
st.subheader("Location activity")
if "store" in filtered.columns:
    agg = {"complaint_id": "count"} if "complaint_id" in filtered.columns else {}
    store_df = filtered.groupby("store").agg(
        total_complaints=("store", "size"),
    )
    if not filtered_classified.empty and "severity" in filtered_classified.columns:
        high_sev = filtered_classified[
            filtered_classified["severity"].astype(str).str.lower().str.contains("high|critical", na=False)
        ]
        high_counts = high_sev.groupby("store").size().rename("high_severity_count")
        store_df = store_df.join(high_counts, how="left").fillna({"high_severity_count": 0})
        store_df["high_severity_count"] = store_df["high_severity_count"].astype(int)
    store_df = store_df.sort_values("total_complaints", ascending=False)
    st.dataframe(store_df, width="stretch", hide_index=False)
else:
    st.info("No `store` column found.")

st.divider()

# ---------------------------------------------------------------------------
# Patterns & Recommendations (Phase 3 output)
# ---------------------------------------------------------------------------
if show_ai_detail:
    st.subheader("AI-generated analysis")
    st.caption("Review this content before using it for operational decisions.")
    patterns_df = data["patterns"]
    if not patterns_df.empty:
        display_cols = [c for c in [
            "title", "trend", "stores_affected", "rec_title",
            "rec_expected_impact", "rec_effort_estimate", "rec_owning_department",
        ] if c in patterns_df.columns]
        st.dataframe(patterns_df[display_cols] if display_cols else patterns_df, width="stretch", hide_index=True)
    else:
        st.info("No recurring patterns are available for the selected data.")

    exec_df = data["exec_summary"]
    if not exec_df.empty and "headline" in exec_df.columns:
        latest = exec_df.sort_values("generated_at").iloc[-1] if "generated_at" in exec_df.columns else exec_df.iloc[-1]
        if pd.notna(latest.get("headline")):
            st.markdown(f"**Latest AI summary:** {latest['headline']}")

st.divider()
st.caption("OpsPilot · data refreshes every five minutes or when manually refreshed.")
