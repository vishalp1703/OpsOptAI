"""
Run this against your real DB to confirm config.py still matches:

    python -m workflows.realtime.schema_check

Read-only diagnostics only — writes nothing.
"""

from sqlalchemy import inspect

from . import config
from .db import get_engine


def check():
    engine = get_engine()
    inspector = inspect(engine)
    real_tables = set(inspector.get_table_names())

    print(f"Connected to: {config.DATABASE_URL}")
    print(f"Found {len(real_tables)} tables.\n")

    print("=== Landing tables (per source) ===")
    for source, cfg in config.SOURCES.items():
        table_name = cfg["landing_table"]
        if table_name not in real_tables:
            print(f"  [MISSING] {source:20s} expected table '{table_name}' — not found. Update config.SOURCES.")
            continue
        real_cols = {c["name"] for c in inspector.get_columns(table_name)}
        expected_cols = (
            {cfg["id_column"], cfg["store_column"], cfg["date_column"], cfg["text_column"]}
            | set(cfg["extra_columns"].values())
            | set(config.COMMON_LANDING_COLUMNS.values())
        )
        missing = expected_cols - real_cols
        if missing:
            print(f"  [COLUMN MISMATCH] {source:20s} table '{table_name}' is missing: {sorted(missing)}")
        else:
            print(f"  [OK] {source:20s} -> '{table_name}'")

    print("\n=== Complaints table ===")
    if config.COMPLAINTS_TABLE not in real_tables:
        print(f"  [MISSING] '{config.COMPLAINTS_TABLE}' not found.")
    else:
        real_cols = {c["name"] for c in inspector.get_columns(config.COMPLAINTS_TABLE)}
        missing = set(config.COMPLAINTS_COLUMNS) - real_cols
        if missing:
            print(f"  [COLUMN MISMATCH] '{config.COMPLAINTS_TABLE}' missing: {sorted(missing)}")
        else:
            print(f"  [OK] '{config.COMPLAINTS_TABLE}'")

    print("\n=== Classification table ===")
    if config.CLASSIFICATION_TABLE not in real_tables:
        print(f"  [MISSING] '{config.CLASSIFICATION_TABLE}' not found.")
    else:
        real_cols = {c["name"] for c in inspector.get_columns(config.CLASSIFICATION_TABLE)}
        missing = set(config.CLASSIFICATION_COLUMNS) - real_cols
        if missing:
            print(f"  [COLUMN MISMATCH] '{config.CLASSIFICATION_TABLE}' missing: {sorted(missing)}")
        else:
            print(f"  [OK] '{config.CLASSIFICATION_TABLE}'")

    print("\n=== Alerts table ===")
    if config.ALERTS_TABLE not in real_tables:
        print(f"  [NOT CREATED YET] '{config.ALERTS_TABLE}' — run: python -m data.init_alerts_table")
    else:
        print(f"  [OK] '{config.ALERTS_TABLE}'")


if __name__ == "__main__":
    check()
