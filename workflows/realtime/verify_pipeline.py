"""
One-shot health check for the whole Phase 6 pipeline — consolidates all the
manual sqlite3 queries we've been running by hand into a single script.

Run:
    python -m workflows.realtime.verify_pipeline

Read-only. Writes nothing. Safe to run anytime, including while the
webhook server / scheduler are running.
"""

from sqlalchemy import func, select

from workflows.realtime import config
from workflows.realtime.db import get_engine, get_table


def _section(title: str):
    print(f"\n=== {title} ===")


def check_landing_tables():
    _section("Landing tables (per source)")
    engine = get_engine()
    for source, cfg in config.SOURCES.items():
        table = get_table(cfg["landing_table"])
        if table is None:
            print(f"  [MISSING] {source:20s} table '{cfg['landing_table']}' not found.")
            continue

        is_processed_col = config.COMMON_LANDING_COLUMNS["is_processed"]
        with engine.begin() as conn:
            total = conn.execute(select(func.count()).select_from(table)).scalar()
            pending = 0
            if is_processed_col in {c.name for c in table.columns}:
                pending = conn.execute(
                    select(func.count()).select_from(table).where(table.c[is_processed_col].is_(False))
                ).scalar()
            last_row = conn.execute(select(table).order_by(table.c.id.desc()).limit(1)).fetchone()

        preview = ""
        if last_row:
            r = dict(last_row._mapping)
            store = r.get(cfg["store_column"], "?")
            text = str(r.get(cfg["text_column"], ""))[:60]
            preview = f" | last: store={store!r} text={text!r}"
        print(f"  {source:20s} total={total:<5} pending_promotion={pending:<5}{preview}")


def check_complaints_and_classification():
    _section("Complaints & classification")
    engine = get_engine()
    complaints_table = get_table(config.COMPLAINTS_TABLE)
    classification_table = get_table(config.CLASSIFICATION_TABLE)

    if complaints_table is None:
        print(f"  [MISSING] '{config.COMPLAINTS_TABLE}' not found.")
        return
    if classification_table is None:
        print(f"  [MISSING] '{config.CLASSIFICATION_TABLE}' not found.")
        return

    with engine.begin() as conn:
        total_complaints = conn.execute(select(func.count()).select_from(complaints_table)).scalar()
        total_classified = conn.execute(select(func.count()).select_from(classification_table)).scalar()
        unclassified = conn.execute(
            select(func.count()).select_from(complaints_table)
            .where(complaints_table.c.complaint_id.not_in(select(classification_table.c.complaint_id)))
        ).scalar()

    print(f"  complaints total:        {total_complaints}")
    print(f"  classified:               {total_classified}")
    print(f"  awaiting classification:  {unclassified}")


def check_alerts():
    _section("Alerts")
    engine = get_engine()
    table = get_table(config.ALERTS_TABLE)
    if table is None:
        print(f"  [MISSING] '{config.ALERTS_TABLE}' not found — run: python -m data.init_alerts_table")
        return

    with engine.begin() as conn:
        open_alerts = conn.execute(
            select(table).where(table.c.status == "open").order_by(table.c.created_at.desc()).limit(20)
        ).fetchall()
        open_count = conn.execute(select(func.count()).select_from(table).where(table.c.status == "open")).scalar()

    print(f"  open alerts: {open_count}")
    for row in open_alerts:
        a = dict(row._mapping)
        print(f"    [{a['trigger_type']}/{a.get('severity')}] {a['source']}: {a['message']}")


if __name__ == "__main__":
    print(f"Connected to: {config.DATABASE_URL}")
    check_landing_tables()
    check_complaints_and_classification()
    check_alerts()
    print()