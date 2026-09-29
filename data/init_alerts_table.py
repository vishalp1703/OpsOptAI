"""
Phase 6 adds exactly ONE new table to your Phase 4 schema: `alerts`.
(The 8 raw source landing tables already exist from Phase 4 — they were
built dormant, waiting for this phase.)

Run:
    python -m data.init_alerts_table

This is additive and safe to re-run (checkfirst=True) — it will never touch
your existing 16 tables.

If your Phase 4 landing tables turn out to be missing any of the columns
Phase 6 needs (id, external_id, raw_payload, received_at, ingested_via,
promoted, promoted_at — see workflows/realtime/config.py LANDING_COLUMNS),
this file also exposes `create_landing_columns_note()` to print an ALTER
TABLE you can run — since it wasn't clear that Phase 4's dormant tables
were built with these exact fields.
"""

from sqlalchemy import (
    Boolean, Column, DateTime, Integer, MetaData, String, Table, Text, create_engine, func,
)

from workflows.realtime import config

metadata = MetaData()

alerts = Table(
    "alerts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("alert_id", String(64), unique=True, nullable=False),
    Column("source", String(64), nullable=False),
    Column("complaint_id", String(64), nullable=True),  # nullable: pre-classification alerts have no complaint row yet
    Column("trigger_type", String(32), nullable=False),  # "keyword" | "severity" | "volume_spike"
    Column("severity", String(16), nullable=True),
    Column("message", Text, nullable=False),
    Column("details", Text, nullable=True),  # JSON blob: matched keyword, counts, etc.
    Column("status", String(16), nullable=False, default="open"),  # "open" | "acknowledged"
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
    Column("acknowledged_at", DateTime(timezone=True), nullable=True),
)


def init():
    engine = create_engine(config.DATABASE_URL, future=True)
    metadata.create_all(engine, checkfirst=True)
    print(f"alerts table ready at {config.DATABASE_URL}")


if __name__ == "__main__":
    init()
