"""
Promotion: raw landing row -> row in `complaints` (still unclassified —
your complaints table has no AI fields; classification lands separately in
complaint_classification via batch_classify.py).

Reads each source's real typed columns (per config.SOURCES) instead of a
JSON blob, since that's how your raw_* tables are actually built.
`is_processed` is your promotion flag (BOOLEAN) — flipped to True once a row
has been written to complaints.

complaint_id is generated as f"{id_prefix}-{business_id}", truncated to 20
chars to fit your VARCHAR(20) column, and prefixed per-source so business
IDs from different sources can never collide even if their raw values
happen to match.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select

from . import config
from .db import get_engine, get_table

logger = logging.getLogger("opspilot.realtime.promotion")


def _build_complaint_id(source: str, business_id: str) -> str:
    """
    Prefix with the source's id_prefix so business IDs from different
    sources can never collide — but skip re-prefixing if the business ID
    already starts with it (e.g. mock webhook payloads already generate
    "TIX-xxxx"), so we don't waste the 20-char budget on "TIX-TIX-xxxx".
    """
    cfg = config.SOURCES[source]
    prefix = cfg["id_prefix"]
    raw = business_id if business_id.upper().startswith(prefix.upper() + "-") else f"{prefix}-{business_id}"
    return raw[:20]


def promote_pending(source: Optional[str] = None, limit: int = 200) -> int:
    """
    Finds unprocessed landing rows (is_processed = False), writes a
    normalized row into `complaints`, and flips is_processed = True.
    Idempotent: a row is only marked processed after the complaints insert
    succeeds, so a crash mid-run just means it's retried next call.
    """
    complaints_table = get_table(config.COMPLAINTS_TABLE)
    if complaints_table is None:
        logger.error("complaints table unavailable — cannot promote anything.")
        return 0

    is_processed_col = config.COMMON_LANDING_COLUMNS["is_processed"]
    auto_id_col = config.COMMON_LANDING_COLUMNS["auto_id"]
    engine = get_engine()
    total_promoted = 0
    already_present = 0

    sources = [source] if source else list(config.SOURCES.keys())
    for src in sources:
        cfg = config.SOURCES[src]
        table_name = cfg["landing_table"]
        landing_table = get_table(table_name)
        if landing_table is None:
            continue
        real_cols = {c.name for c in landing_table.columns}
        if is_processed_col not in real_cols:
            logger.warning("Landing table '%s' has no '%s' column — skipping promotion for this source.", table_name, is_processed_col)
            continue

        with engine.begin() as conn:
            pending_rows = conn.execute(
                select(landing_table)
                .where(landing_table.c[is_processed_col].is_(False))
                .limit(limit)
            ).fetchall()

            for row in pending_rows:
                row_dict = dict(row._mapping)
                business_id = row_dict.get(cfg["id_column"])
                complaint_id = _build_complaint_id(src, str(business_id))

                normalized = {
                    "complaint_id": complaint_id,
                    "source": src,
                    "store": row_dict.get(cfg["store_column"]),
                    "date": row_dict.get(cfg["date_column"]),
                    "customer_text": row_dict.get(cfg["text_column"]) or "",
                    "source_table": table_name,
                    "source_row_id": row_dict.get(auto_id_col),
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
                normalized = {k: v for k, v in normalized.items() if k in config.COMPLAINTS_COLUMNS}

                # External systems can replay events and source exports can
                # contain duplicate business IDs. A complaint already present
                # in the analytics store is therefore a successful no-op, not
                # a reason to roll back every pending landing row.
                exists = conn.execute(
                    select(complaints_table.c.complaint_id)
                    .where(complaints_table.c.complaint_id == complaint_id)
                    .limit(1)
                ).scalar_one_or_none()
                if exists is None:
                    conn.execute(complaints_table.insert().values(**normalized))
                    total_promoted += 1
                else:
                    already_present += 1
                conn.execute(
                    landing_table.update()
                    .where(landing_table.c[auto_id_col] == row_dict[auto_id_col])
                    .values(**{is_processed_col: True})
                )

    if total_promoted:
        logger.info("Promoted %d landing rows into complaints (unclassified).", total_promoted)
    if already_present:
        logger.info("Marked %d duplicate landing row(s) processed; complaint already existed.", already_present)
    return total_promoted
