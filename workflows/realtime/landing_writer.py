"""
Single entry point for getting a raw record — from either a poller or a
webhook — safely into its real landing table.

Your raw_* tables have typed, source-specific columns (not a JSON blob), so
this maps a payload's LOGICAL keys onto the REAL column names declared in
config.py's SOURCES[source]. Connectors and webhook payloads can use either
the logical key ("store", "date", "text", "id") or the real column name
directly — whichever's present wins, logical key first. This mirrors Phase
1's "fuzzy matching so naming differences don't break it" philosophy.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from . import config
from .db import get_engine, get_table

logger = logging.getLogger("opspilot.realtime.landing_writer")


class LandingWriteError(Exception):
    pass


def _extract(payload: dict, logical_key: str, real_column: str):
    if logical_key in payload and payload[logical_key] not in (None, ""):
        return payload[logical_key]
    if real_column in payload and payload[real_column] not in (None, ""):
        return payload[real_column]
    return None


def write_raw_record(source: str, payload: dict, ingested_via: str = "unknown") -> Optional[int]:
    """
    Insert one raw record into the real landing table for `source`, mapping
    payload fields onto that source's real columns per config.SOURCES.

    Returns the new row's auto `id`, or None if the write was skipped (e.g.
    table missing) — callers should log and move on, never crash the
    poller/webhook server over one bad source.

    `ingested_via` is for logging only — your schema has no column for it.
    """
    if source not in config.SOURCES:
        raise LandingWriteError(f"Unknown source '{source}'. Check workflows/realtime/config.py SOURCES.")

    cfg = config.SOURCES[source]
    table_name = cfg["landing_table"]
    table = get_table(table_name)
    if table is None:
        logger.error("Cannot write '%s' record — landing table '%s' unavailable.", source, table_name)
        return None

    real_cols = {c.name for c in table.columns}
    common = config.COMMON_LANDING_COLUMNS

    row = {}

    business_id = _extract(payload, "id", cfg["id_column"])
    row[cfg["id_column"]] = str(business_id) if business_id else f"{cfg['id_prefix']}-{uuid.uuid4().hex[:10].upper()}"

    store_val = _extract(payload, "store", cfg["store_column"])
    if store_val is not None:
        row[cfg["store_column"]] = store_val

    date_val = _extract(payload, "date", cfg["date_column"])
    row[cfg["date_column"]] = date_val or datetime.now(timezone.utc).isoformat()

    text_val = _extract(payload, "text", cfg["text_column"])
    row[cfg["text_column"]] = text_val or ""

    for logical_key, real_col in cfg["extra_columns"].items():
        val = _extract(payload, logical_key, real_col)
        if val is not None:
            row[real_col] = val

    if common["ingested_at"] in real_cols:
        row[common["ingested_at"]] = datetime.now(timezone.utc).isoformat()
    if common["is_processed"] in real_cols:
        row[common["is_processed"]] = False

    # Only keep columns that actually exist on the reflected table.
    row = {k: v for k, v in row.items() if k in real_cols}

    engine = get_engine()
    with engine.begin() as conn:
        result = conn.execute(table.insert().values(**row))
        new_id = result.inserted_primary_key[0] if result.inserted_primary_key else None

    logger.info("Landed %s record (id=%s, business_id=%s, via=%s) into '%s'.",
                source, new_id, row.get(cfg["id_column"]), ingested_via, table_name)
    return new_id
