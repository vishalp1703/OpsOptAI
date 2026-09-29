"""
Scheduled batch classification against your REAL schema: complaints and
complaint_classification are separate tables. "Unclassified" means a
complaints row with no matching complaint_classification.complaint_id —
found here via a LEFT JOIN / NOT IN rather than checking a sentiment column
on complaints (that column doesn't exist there).

Tries to import your real Phase 2 `agents/classification_agent.py`. If that
import fails (e.g. OPENROUTER_API_KEY not set, or Phase 6 not yet merged
into your repo), logs a clear skip and does nothing destructive — rows stay
queued for the next run.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select

from . import config
from .alerting import check_severity
from .db import get_engine, get_table

logger = logging.getLogger("opspilot.realtime.batch_classify")


def _load_real_agent():
    try:
        from agents.classification_agent import ClassificationAgent  # your real Phase 2 module
        return ClassificationAgent()
    except Exception as e:
        logger.warning(
            "Could not import your real agents.classification_agent.ClassificationAgent (%s). "
            "Batch classification skipped this run — rows stay queued, nothing is lost.", e,
        )
        return None


def _find_unclassified(engine, complaints_table, classification_table, limit: int):
    classified_ids = select(classification_table.c.complaint_id)
    with engine.begin() as conn:
        rows = conn.execute(
            select(complaints_table)
            .where(complaints_table.c.complaint_id.not_in(classified_ids))
            .limit(limit)
        ).fetchall()
    return rows


def run_batch_classification(limit: Optional[int] = None) -> int:
    limit = limit or config.BATCH_CLASSIFY_MAX_ROWS_PER_RUN
    complaints_table = get_table(config.COMPLAINTS_TABLE)
    classification_table = get_table(config.CLASSIFICATION_TABLE)
    if complaints_table is None or classification_table is None:
        logger.error("complaints or complaint_classification table unavailable — skipping batch classification.")
        return 0

    engine = get_engine()
    pending = _find_unclassified(engine, complaints_table, classification_table, limit)
    if not pending:
        return 0

    agent = _load_real_agent()
    if agent is None:
        return 0

    classified_count = 0
    for row in pending:
        row_dict = dict(row._mapping)
        try:
            # ClassificationAgent exposes classify_complaint(), not a
            # classify_one() convenience method. Keep this adapter here so
            # the realtime scheduler and the batch CLI use the same agent.
            result = agent.classify_complaint(
                customer_text=row_dict.get("customer_text", ""),
                store=row_dict.get("store"),
                source=row_dict.get("source"),
                complaint_id=row_dict["complaint_id"],
            )
        except Exception:
            logger.exception("Classification failed for complaint_id=%s — leaving unclassified for retry.", row_dict.get("complaint_id"))
            continue

        classification_row = {
            "complaint_id": row_dict["complaint_id"],
            "category": result.category.value,
            "sentiment": result.sentiment.value,
            "severity": result.severity.value,
            "department": result.department.value,
            "confidence": result.confidence,
            "reasoning": result.reasoning,
            "model_used": result.model_used,
            "classified_at": result.classified_at or datetime.now(timezone.utc).isoformat(),
        }
        classification_row = {k: v for k, v in classification_row.items() if k in config.CLASSIFICATION_COLUMNS}

        with engine.begin() as conn:
            conn.execute(classification_table.insert().values(**classification_row))
        classified_count += 1

        if classification_row.get("severity"):
            check_severity(
                complaint_id=row_dict["complaint_id"],
                source=row_dict.get("source", "unknown"),
                severity=classification_row["severity"],
                store=row_dict.get("store"),
            )

    logger.info("Batch classified %d/%d pending complaints.", classified_count, len(pending))
    return classified_count
