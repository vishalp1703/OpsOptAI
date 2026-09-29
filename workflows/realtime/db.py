"""
Shared DB access for Phase 6.

Follows the same philosophy as dashboard/data_access.py from Phase 5:
reflect real tables at runtime instead of hard-importing data/models.py, so
Phase 6 never crashes the whole process over a column-name mismatch — it
flags a clear, specific warning instead.
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import MetaData, Table, create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.exc import NoSuchTableError

from . import config

logger = logging.getLogger("opspilot.realtime.db")

_engine: Optional[Engine] = None
_metadata: Optional[MetaData] = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = create_engine(config.DATABASE_URL, future=True)
    return _engine


def get_table(table_name: str) -> Optional[Table]:
    """
    Reflect a single table by name. Returns None (and logs a warning)
    instead of raising, so callers can degrade gracefully — e.g. skip a
    source whose landing table doesn't exist yet rather than crash the
    whole scheduler.
    """
    global _metadata
    engine = get_engine()
    if _metadata is None:
        _metadata = MetaData()
    try:
        # autoload_with re-reads from DB if not already cached in _metadata
        if table_name in _metadata.tables:
            return _metadata.tables[table_name]
        return Table(table_name, _metadata, autoload_with=engine)
    except NoSuchTableError:
        logger.warning(
            "Table '%s' not found in DB at %s. Check workflows/realtime/config.py "
            "LANDING_TABLES mapping against your real data/models.py.",
            table_name, config.DATABASE_URL,
        )
        return None


def table_exists(table_name: str) -> bool:
    return get_table(table_name) is not None


def refresh_metadata():
    """Call after creating new tables (e.g. alerts) so reflection picks them up."""
    global _metadata
    _metadata = None
