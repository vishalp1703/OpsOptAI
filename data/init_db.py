"""
data/init_db.py

Standalone script: creates every table defined in data/models.py against
the database pointed to by DATABASE_URL (defaults to the dev SQLite file
at data/opspilot.db -- see data/database.py).

Run:
    python -m data.init_db                  # create tables, keep existing data
    python -m data.init_db --drop           # DESTRUCTIVE: drop + recreate everything
    python -m data.init_db --load-raw       # also bulk-load data/raw/*.csv (run
                                             # data/generate_sample_data.py first)
    python -m data.init_db --drop --load-raw

Must be run as a module (`python -m data.init_db`), not `python data/init_db.py`,
so the `agents`/`data` package imports resolve correctly regardless of cwd.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from data.database import PROJECT_ROOT, engine, init_db, session_scope
from data.models import RAW_TABLE_REGISTRY  # noqa: F401 (import registers tables on Base.metadata)


def load_raw_csvs(raw_dir: Path | None = None) -> None:
    """
    Bulk-loads every data/raw/<source_key>.csv produced by
    generate_sample_data.py into its matching raw_* table.

    Idempotent-ish for demo purposes: does NOT dedupe against existing
    rows (raw landing tables are an append-only log by design -- Phase 1's
    cleaning step is where true deduplication happens). Re-running this
    against the same CSVs will insert duplicate raw rows, which is fine
    for a dev DB you can just --drop and recreate.
    """
    raw_dir = raw_dir or (PROJECT_ROOT / "data" / "raw")
    if not raw_dir.exists():
        print(f"⚠️  {raw_dir} does not exist. Run data/generate_sample_data.py first.")
        return

    with session_scope() as db:
        for source_key, table_cls in RAW_TABLE_REGISTRY.items():
            csv_path = raw_dir / f"{source_key}.csv"
            if not csv_path.exists():
                print(f"⚠️  Skipping {source_key}: {csv_path} not found")
                continue

            df = pd.read_csv(csv_path, dtype=str)  # dtype=str: raw layer stays untyped/unclean on purpose
            # NaN -> None so SQLite/Postgres stores real NULLs, not the string "nan"
            df = df.where(pd.notnull(df), None)

            rows = [table_cls(**row) for row in df.to_dict(orient="records")]
            db.add_all(rows)
            print(f"✅ {source_key:<20} {len(rows):>4} rows -> {table_cls.__tablename__}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Initialize the OpsPilot AI database")
    parser.add_argument("--drop", action="store_true", help="Drop all tables before creating (destructive)")
    parser.add_argument("--load-raw", action="store_true", help="Bulk-load data/raw/*.csv after creating tables")
    args = parser.parse_args()

    print(f"Using database: {engine.url}")
    init_db(drop_first=args.drop)
    print("✅ Tables created." if not args.drop else "✅ Tables dropped and recreated.")

    if args.load_raw:
        load_raw_csvs()


if __name__ == "__main__":
    main()
