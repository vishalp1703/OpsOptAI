"""
Alert rule engine + pluggable notification channels.

Three trigger types, matching what real operators actually need:
  - keyword   : fires the instant a raw record lands, before classification
                (safety/legal terms in incidents, social media, reviews)
  - severity  : fires right after Phase 2/3 classifies a complaint as
                High/Critical severity
  - volume_spike : fires when a (store, category) pair crosses a threshold
                within a rolling window — reuses the same spirit as Phase 5's
                Operational Risk Score, but as an event trigger rather than a
                dashboard number

Notification channels implement the `Notifier` interface below. Today:
  - DashboardNotifier is always on — writes to the `alerts` table, and
    dashboard/live_panel.py reads from it.
  - EmailNotifier activates automatically once SMTP_* env vars are set
    (see config.py); otherwise it logs and no-ops instead of crashing.
  - Add Slack/Teams/PagerDuty later: implement `send()`, add to
    ACTIVE_NOTIFIERS. Nothing else in this file changes.
"""

from __future__ import annotations

import json
import logging
import smtplib
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from typing import Optional

from sqlalchemy import select

from . import config
from .db import get_engine, get_table

logger = logging.getLogger("opspilot.realtime.alerting")


# ---------------------------------------------------------------------------
# Alert dataclass-ish dict + notifier interface
# ---------------------------------------------------------------------------
class Notifier(ABC):
    @abstractmethod
    def send(self, alert: dict) -> None:
        ...


class DashboardNotifier(Notifier):
    """Writes the alert to the `alerts` table. Always active."""

    def send(self, alert: dict) -> None:
        table = get_table(config.ALERTS_TABLE)
        if table is None:
            logger.error(
                "alerts table not found — run `python -m data.init_alerts_table` first. "
                "Alert dropped: %s", alert["message"],
            )
            return
        engine = get_engine()
        with engine.begin() as conn:
            conn.execute(table.insert().values(
                alert_id=alert["alert_id"],
                source=alert["source"],
                complaint_id=alert.get("complaint_id"),
                trigger_type=alert["trigger_type"],
                severity=alert.get("severity"),
                message=alert["message"],
                details=json.dumps(alert.get("details", {}), default=str),
                status="open",
            ))


class EmailNotifier(Notifier):
    """No-ops safely if SMTP isn't configured — lets you turn this on later
    just by setting env vars, no code change."""

    def is_configured(self) -> bool:
        return bool(config.SMTP_HOST and config.ALERT_EMAIL_TO)

    def send(self, alert: dict) -> None:
        if not self.is_configured():
            logger.info("EmailNotifier not configured (SMTP_HOST/ALERT_EMAIL_TO unset) — skipping email for: %s", alert["message"])
            return
        msg = MIMEText(f"{alert['message']}\n\nDetails: {json.dumps(alert.get('details', {}), indent=2, default=str)}")
        msg["Subject"] = f"[OpsPilot Alert][{alert.get('severity', 'N/A')}] {alert['trigger_type']} — {alert['source']}"
        msg["From"] = config.ALERT_EMAIL_FROM
        msg["To"] = ", ".join(config.ALERT_EMAIL_TO)
        try:
            with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=10) as server:
                server.starttls()
                if config.SMTP_USER:
                    server.login(config.SMTP_USER, config.SMTP_PASSWORD)
                server.sendmail(config.ALERT_EMAIL_FROM, config.ALERT_EMAIL_TO, msg.as_string())
        except Exception:
            logger.exception("Failed to send alert email for: %s", alert["message"])


# Add new channels here as you build them (SlackNotifier, etc.)
ACTIVE_NOTIFIERS: list[Notifier] = [DashboardNotifier(), EmailNotifier()]


def _dispatch(alert: dict) -> dict:
    alert.setdefault("alert_id", str(uuid.uuid4()))
    for notifier in ACTIVE_NOTIFIERS:
        try:
            notifier.send(alert)
        except Exception:
            logger.exception("Notifier %s failed on alert %s", type(notifier).__name__, alert["alert_id"])
    logger.warning("ALERT [%s/%s] %s", alert["trigger_type"], alert.get("severity"), alert["message"])
    return alert


# ---------------------------------------------------------------------------
# Trigger 1: keyword (runs synchronously in the webhook handler / poll loop)
# ---------------------------------------------------------------------------
def check_immediate_keywords(source: str, payload: dict) -> Optional[dict]:
    keywords = config.IMMEDIATE_ALERT_KEYWORDS.get(source)
    if not keywords:
        return None

    text_blob = " ".join(str(v) for v in payload.values() if isinstance(v, str)).lower()
    for kw in keywords:
        if kw.lower() in text_blob:
            return _dispatch({
                "source": source,
                "trigger_type": "keyword",
                "severity": "High",
                "message": f"Keyword tripwire '{kw}' matched in incoming {source} record.",
                "details": {"matched_keyword": kw, "payload_preview": text_blob[:200]},
            })
    return None


# ---------------------------------------------------------------------------
# Trigger 2: severity (call after Phase 2/3 classification finishes a row)
# ---------------------------------------------------------------------------
SEVERITY_ORDER = ["Low", "Medium", "High", "Critical"]


def check_severity(complaint_id: str, source: str, severity: str, store: Optional[str] = None) -> Optional[dict]:
    try:
        if SEVERITY_ORDER.index(severity) < SEVERITY_ORDER.index(config.SEVERITY_ALERT_THRESHOLD):
            return None
    except ValueError:
        logger.warning("Unrecognized severity value '%s' — skipping severity alert check.", severity)
        return None

    return _dispatch({
        "source": source,
        "trigger_type": "severity",
        "severity": severity,
        "complaint_id": complaint_id,
        "message": f"Complaint {complaint_id} classified as {severity} severity" + (f" at {store}." if store else "."),
        "details": {"store": store},
    })


# ---------------------------------------------------------------------------
# Trigger 3: volume spike (call periodically from the scheduler)
# ---------------------------------------------------------------------------
def _parse_dt(value) -> Optional[datetime]:
    """complaints.created_at / date are VARCHAR — parse defensively rather
    than trusting SQL date comparisons on strings."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def check_volume_spikes() -> list[dict]:
    """
    Joins `complaints` -> `complaint_classification` (category lives only
    on the classification table in your schema) and fires one alert per
    (store, category) pair that crosses the threshold within the rolling
    window. Recency is based on complaints.created_at, parsed in Python
    since it's stored as VARCHAR rather than a real datetime type.
    """
    complaints_table = get_table(config.COMPLAINTS_TABLE)
    classification_table = get_table(config.CLASSIFICATION_TABLE)
    if complaints_table is None or classification_table is None:
        return []

    window_start = datetime.now(timezone.utc) - timedelta(minutes=config.VOLUME_SPIKE_WINDOW_MINUTES)
    engine = get_engine()
    with engine.begin() as conn:
        rows = conn.execute(
            select(complaints_table.c.store, complaints_table.c.created_at, classification_table.c.category)
            .join(classification_table, complaints_table.c.complaint_id == classification_table.c.complaint_id)
        ).fetchall()

    counts: dict[tuple, int] = {}
    for store, created_at, category in rows:
        dt = _parse_dt(created_at)
        if dt is None or dt < window_start:
            continue
        key = (store, category)
        counts[key] = counts.get(key, 0) + 1

    fired = []
    for (store, category), n in counts.items():
        if n >= config.VOLUME_SPIKE_THRESHOLD:
            fired.append(_dispatch({
                "source": "pattern_detection",
                "trigger_type": "volume_spike",
                "severity": "Medium",
                "message": f"{n} complaints in '{category}' at '{store}' in the last "
                           f"{config.VOLUME_SPIKE_WINDOW_MINUTES} minutes (threshold: {config.VOLUME_SPIKE_THRESHOLD}).",
                "details": {"store": store, "category": category, "count": n},
            }))
    return fired
