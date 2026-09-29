"""
Phase 6 addition to the Phase 5 dashboard: a "Live Ops" panel showing
per-source ingestion health and an open-alerts feed.

Wire into dashboard/app.py with:

    from dashboard.live_panel import render_live_panel
    ...
    render_live_panel(engine)   # pass the same SQLAlchemy engine app.py already builds

Follows Phase 5's established pattern: reflect tables at runtime, degrade
to a visible "not available" notice instead of crashing the whole dashboard
if a table/column is missing.
"""

from __future__ import annotations

import json

import pandas as pd
import streamlit as st
from sqlalchemy import inspect, select

from workflows.realtime import config


def _reflect(engine, table_name):
    from sqlalchemy import MetaData, Table
    try:
        return Table(table_name, MetaData(), autoload_with=engine)
    except Exception:
        return None


def render_live_panel(engine):
    st.subheader("Live Ops")

    inspector = inspect(engine)
    real_tables = set(inspector.get_table_names())

    # --- Per-source ingestion health -------------------------------------
    st.markdown("**Ingestion status by source**")
    rows = []
    for source, cfg in config.SOURCES.items():
        table_name = cfg["landing_table"]
        if table_name not in real_tables:
            rows.append({"source": source, "mode": cfg["mode"].value, "status": "table not found", "pending": "-", "last_received": "-"})
            continue
        table = _reflect(engine, table_name)
        cols = {c.name for c in table.columns}
        processed_col = config.COMMON_LANDING_COLUMNS["is_processed"]
        ingested_col = config.COMMON_LANDING_COLUMNS["ingested_at"]
        with engine.begin() as conn:
            pending = "-"
            last_received = "-"
            if processed_col in cols:
                pending = len(conn.execute(select(table).where(table.c[processed_col].is_(False))).fetchall())
            if ingested_col in cols:
                res = conn.execute(select(table.c[ingested_col]).order_by(table.c[ingested_col].desc()).limit(1)).fetchone()
                last_received = res[0] if res else "-"
        rows.append({
            "source": source, "mode": cfg["mode"].value, "status": "ok",
            "pending": pending, "last_received": str(last_received),
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    # --- Alerts feed -------------------------------------------------------
    st.markdown("**Open alerts**")
    if config.ALERTS_TABLE not in real_tables:
        st.info("Alerts table not created yet — run `python -m data.init_alerts_table`.")
        return

    alerts_table = _reflect(engine, config.ALERTS_TABLE)
    with engine.begin() as conn:
        open_alerts = conn.execute(
            select(alerts_table).where(alerts_table.c.status == "open").order_by(alerts_table.c.created_at.desc()).limit(50)
        ).fetchall()

    if not open_alerts:
        st.success("No open alerts.")
        return

    severity_color = {"Critical": "🔴", "High": "🟠", "Medium": "🟡", "Low": "🟢"}
    for alert in open_alerts:
        a = dict(alert._mapping)
        icon = severity_color.get(a.get("severity"), "⚪")
        with st.expander(f"{icon} [{a['trigger_type']}] {a['message']}"):
            st.write(f"Source: {a['source']} | Severity: {a.get('severity')} | Created: {a.get('created_at')}")
            if a.get("details"):
                try:
                    st.json(json.loads(a["details"]))
                except (TypeError, json.JSONDecodeError):
                    st.write(a["details"])
            if st.button("Acknowledge", key=f"ack_{a['alert_id']}"):
                with engine.begin() as conn:
                    conn.execute(
                        alerts_table.update()
                        .where(alerts_table.c.alert_id == a["alert_id"])
                        .values(status="acknowledged")
                    )
                st.rerun()
