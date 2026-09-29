"""
Central scheduler — the one process you run alongside the webhook server to
get real-time ingestion end to end. Runs three kinds of jobs:

  1. Polling jobs — one per polling source (pos_logs, crm_notes,
     employee_feedback, surveys), each on its own interval from config.py.
  2. Promotion job — periodically moves any unpromoted landing rows
     (from either polling or webhooks) into `complaints`.
  3. Batch classification job — periodically classifies newly-promoted rows
     via your real Phase 2 agent, then runs the volume-spike alert check.

Run:
    python -m workflows.realtime.scheduler

Run the webhook server in a separate process (uvicorn) alongside this.
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.blocking import BlockingScheduler

from . import config
from .alerting import check_immediate_keywords, check_volume_spikes
from .batch_classify import run_batch_classification
from .connectors import POLLING_CONNECTORS
from .landing_writer import write_raw_record
from .promotion import promote_pending

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("opspilot.realtime.scheduler")

# In-memory cursor store. Fine for a single-process mock scheduler; if you
# run multiple worker processes against real APIs later, persist cursors in
# the DB (one row per source) instead so restarts don't reprocess data.
_cursors: dict[str, int] = {}
_connector_instances = {name: cls() for name, cls in POLLING_CONNECTORS.items()}


def poll_source(source_name: str):
    connector = _connector_instances[source_name]
    cursor = _cursors.get(source_name)
    try:
        records, next_cursor = connector.fetch_new(cursor)
    except Exception:
        logger.exception("Polling '%s' failed — will retry next interval.", source_name)
        return
    _cursors[source_name] = next_cursor

    for record in records:
        row_id = write_raw_record(source=source_name, payload=record, ingested_via="poll")
        if row_id is not None:
            check_immediate_keywords(source_name, record)

    if records:
        logger.info("Polled '%s': %d new record(s).", source_name, len(records))


def promotion_job():
    promote_pending()


def classification_and_spike_job():
    run_batch_classification()
    check_volume_spikes()


def build_scheduler() -> BlockingScheduler:
    scheduler = BlockingScheduler()

    for source_name, connector_cls in POLLING_CONNECTORS.items():
        interval = config.SOURCES[source_name]["poll_interval_seconds"]
        scheduler.add_job(
            poll_source, "interval", seconds=interval, args=[source_name],
            id=f"poll_{source_name}", max_instances=1, coalesce=True,
        )
        logger.info("Scheduled polling for '%s' every %ds.", source_name, interval)

    scheduler.add_job(
        promotion_job, "interval", seconds=60,
        id="promotion", max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        classification_and_spike_job, "interval",
        seconds=config.BATCH_CLASSIFY_INTERVAL_SECONDS,
        id="batch_classify", max_instances=1, coalesce=True,
    )
    return scheduler


if __name__ == "__main__":
    sched = build_scheduler()
    logger.info("Starting OpsPilot realtime scheduler. Ctrl+C to stop.")
    try:
        sched.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped.")
