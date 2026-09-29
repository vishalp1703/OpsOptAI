"""
Phase 6 test suite, built against your REAL schema as confirmed by
dump_schema.py on 2026-08-14 (see chat) — raw_pos_logs, raw_crm_notes,
raw_employee_feedback, raw_surveys, raw_customer_reviews, raw_support_tickets,
raw_social_media, raw_incident_reports, complaints, complaint_classification,
alerts.

Run:
    python -m pytest tests/test_phase6.py -v
    (or just: python tests/test_phase6.py)
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TMP_DB_FD, TMP_DB_PATH = tempfile.mkstemp(suffix=".db")
os.environ["DATABASE_URL"] = f"sqlite:///{TMP_DB_PATH}"
os.environ["WEBHOOK_SHARED_SECRET"] = "test-secret"

from sqlalchemy import (  # noqa: E402
    Boolean, Column, DateTime, Float, Integer, MetaData, String, Table, Text,
    create_engine, func,
)

from workflows.realtime import config, db  # noqa: E402


def build_test_schema(engine):
    """Mirrors your real column names/types exactly, per dump_schema.py."""
    metadata = MetaData()

    Table("raw_pos_logs", metadata,
        Column("log_id", String(20)), Column("store_id", String(100)),
        Column("transaction_date", String(30)), Column("exception_type", String(30)),
        Column("amount", Float), Column("cashier_id", String(20)), Column("notes", Text),
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("ingested_at", String(40)), Column("is_processed", Boolean, default=False))

    Table("raw_crm_notes", metadata,
        Column("note_id", String(20)), Column("account", String(100)),
        Column("note_date", String(30)), Column("rep_name", String(120)), Column("note_text", Text),
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("ingested_at", String(40)), Column("is_processed", Boolean, default=False))

    Table("raw_employee_feedback", metadata,
        Column("feedback_id", String(20)), Column("store", String(100)),
        Column("submission_date", String(30)), Column("employee_role", String(30)),
        Column("feedback_text", Text),
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("ingested_at", String(40)), Column("is_processed", Boolean, default=False))

    Table("raw_surveys", metadata,
        Column("survey_id", String(20)), Column("store", String(100)),
        Column("response_date", String(30)), Column("nps_score", Integer), Column("comments", Text),
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("ingested_at", String(40)), Column("is_processed", Boolean, default=False))

    Table("raw_customer_reviews", metadata,
        Column("review_id", String(20)), Column("store_location", String(100)),
        Column("review_date", String(30)), Column("rating", Integer), Column("review_text", Text),
        Column("platform", String(30)),
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("ingested_at", String(40)), Column("is_processed", Boolean, default=False))

    Table("raw_support_tickets", metadata,
        Column("ticket_id", String(20)), Column("store", String(100)),
        Column("created_date", String(30)), Column("priority", String(20)),
        Column("subject", String(255)), Column("description", Text), Column("status", String(20)),
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("ingested_at", String(40)), Column("is_processed", Boolean, default=False))

    Table("raw_social_media", metadata,
        Column("post_id", String(20)), Column("store_mentioned", String(100)),
        Column("post_date", String(30)), Column("platform", String(30)), Column("text", Text),
        Column("likes", Integer),
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("ingested_at", String(40)), Column("is_processed", Boolean, default=False))

    Table("raw_incident_reports", metadata,
        Column("incident_id", String(20)), Column("store", String(100)),
        Column("incident_date", String(30)), Column("severity", String(20)),
        Column("category", String(50)), Column("description", Text),
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("ingested_at", String(40)), Column("is_processed", Boolean, default=False))

    Table("complaints", metadata,
        Column("complaint_id", String(20), primary_key=True),
        Column("source", String(50)), Column("store", String(100)), Column("date", String(30)),
        Column("customer_text", Text), Column("source_table", String(50)),
        Column("source_row_id", Integer), Column("created_at", String(40)))

    Table("complaint_classification", metadata,
        Column("complaint_id", String(20), primary_key=True),
        Column("category", String(20)), Column("sentiment", String(8)), Column("severity", String(8)),
        Column("department", String(16)), Column("confidence", Float),
        Column("reasoning", String(320)), Column("model_used", String(80)),
        Column("classified_at", String(40)))

    Table(config.ALERTS_TABLE, metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("alert_id", String(64), unique=True), Column("source", String(64)),
        Column("complaint_id", String(64), nullable=True), Column("trigger_type", String(32)),
        Column("severity", String(16), nullable=True), Column("message", Text),
        Column("details", Text, nullable=True), Column("status", String(16), default="open"),
        Column("created_at", DateTime(timezone=True), server_default=func.now()),
        Column("acknowledged_at", DateTime(timezone=True), nullable=True))

    metadata.create_all(engine)


def setup_module(_module=None):
    engine = create_engine(config.DATABASE_URL, future=True)
    build_test_schema(engine)
    db.refresh_metadata()


def test_webhook_lands_and_fires_keyword_alert():
    from fastapi.testclient import TestClient
    from workflows.realtime.webhook_server import app

    client = TestClient(app)
    resp = client.post(
        "/webhooks/incidents",
        json={"incident_id": "INC-1", "store": "Downtown", "description": "Customer had a minor injury near the entrance.", "severity": "High", "category": "Safety"},
        headers={config.WEBHOOK_HEADER_NAME: config.WEBHOOK_SHARED_SECRET},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["alert_fired"] is True

    table = db.get_table("raw_incident_reports")
    engine = db.get_engine()
    with engine.begin() as conn:
        rows = conn.execute(table.select()).fetchall()
    assert len(rows) == 1
    row = dict(rows[0]._mapping)
    assert row["store"] == "Downtown"
    assert row["is_processed"] is False


def test_webhook_rejects_bad_secret():
    from fastapi.testclient import TestClient
    from workflows.realtime.webhook_server import app

    client = TestClient(app)
    resp = client.post("/webhooks/incidents", json={"incident_id": "evt-x"}, headers={config.WEBHOOK_HEADER_NAME: "wrong"})
    assert resp.status_code == 401


def test_polling_connector_lands_records():
    from workflows.realtime.connectors import CRMConnector
    from workflows.realtime.landing_writer import write_raw_record

    connector = CRMConnector(seed=42)
    records, _cursor = connector.fetch_new()
    for r in records:
        write_raw_record("crm_notes", r, ingested_via="poll")

    table = db.get_table("raw_crm_notes")
    engine = db.get_engine()
    with engine.begin() as conn:
        rows = conn.execute(table.select()).fetchall()
    assert len(rows) == len(records)
    for row in rows:
        assert dict(row._mapping)["account"] is not None  # store mapped onto CRM's real 'account' column


def test_promotion_moves_landing_to_complaints_and_flips_is_processed():
    from workflows.realtime.promotion import promote_pending

    promoted = promote_pending()
    assert promoted >= 1

    complaints_table = db.get_table("complaints")
    engine = db.get_engine()
    with engine.begin() as conn:
        rows = conn.execute(complaints_table.select()).fetchall()
    assert len(rows) >= 1
    for row in rows:
        r = dict(row._mapping)
        assert r["complaint_id"] is not None
        assert len(r["complaint_id"]) <= 20

    # is_processed should now be flipped True on the source landing rows
    incidents_table = db.get_table("raw_incident_reports")
    with engine.begin() as conn:
        incident_rows = conn.execute(incidents_table.select()).fetchall()
    assert all(dict(r._mapping)["is_processed"] is True for r in incident_rows)

    # No sentiment/category exist on complaints — classification lives elsewhere
    classification_table = db.get_table("complaint_classification")
    with engine.begin() as conn:
        classified = conn.execute(classification_table.select()).fetchall()
    assert len(classified) == 0  # nothing classified yet, as designed


def test_promotion_is_idempotent_for_replayed_source_records():
    """A replayed webhook/source export must not fail on an existing complaint ID."""
    from workflows.realtime.landing_writer import write_raw_record
    from workflows.realtime.promotion import promote_pending

    write_raw_record(
        "incidents",
        {"id": "INC-1", "store": "Downtown", "text": "Replayed incident event."},
        ingested_via="test-replay",
    )
    assert promote_pending(source="incidents") == 0

    incidents_table = db.get_table("raw_incident_reports")
    engine = db.get_engine()
    with engine.begin() as conn:
        replay = conn.execute(
            incidents_table.select().where(incidents_table.c.incident_id == "INC-1")
            .order_by(incidents_table.c.id.desc()).limit(1)
        ).fetchone()
    assert dict(replay._mapping)["is_processed"] is True


def test_batch_classifier_uses_the_actual_agent_interface(monkeypatch=None):
    """The scheduler must call ClassificationAgent.classify_complaint()."""
    from datetime import datetime, timezone

    from agents.schemas import Category, ClassificationResult, Department, Sentiment, Severity
    from workflows.realtime import batch_classify

    now = datetime.now(timezone.utc).isoformat()
    complaints_table = db.get_table("complaints")
    engine = db.get_engine()
    with engine.begin() as conn:
        conn.execute(complaints_table.insert().values(
            complaint_id="BATCH-1", source="support_tickets", store="Downtown", date=now,
            customer_text="The checkout system is unavailable.", source_table="test",
            source_row_id=999, created_at=now,
        ))

    class StubAgent:
        def classify_complaint(self, customer_text, store, source, complaint_id):
            assert complaint_id
            assert customer_text
            return ClassificationResult(
                complaint_id=complaint_id,
                category=Category.TECHNICAL_POS,
                sentiment=Sentiment.NEGATIVE,
                severity=Severity.HIGH,
                department=Department.IT,
                confidence=0.95,
                reasoning="Test result",
                model_used="stub",
                classified_at=now,
            )

    if monkeypatch:
        monkeypatch.setattr(batch_classify, "_load_real_agent", lambda: StubAgent())
        assert batch_classify.run_batch_classification(limit=1) == 1
    else:
        original = batch_classify._load_real_agent
        try:
            batch_classify._load_real_agent = lambda: StubAgent()
            assert batch_classify.run_batch_classification(limit=1) == 1
        finally:
            batch_classify._load_real_agent = original


def test_volume_spike_alert_fires():
    from datetime import datetime, timezone

    from workflows.realtime.alerting import check_volume_spikes

    complaints_table = db.get_table("complaints")
    classification_table = db.get_table("complaint_classification")
    engine = db.get_engine()
    now_iso = datetime.now(timezone.utc).isoformat()
    with engine.begin() as conn:
        for i in range(config.VOLUME_SPIKE_THRESHOLD):
            cid = f"SPIKE-{i}"
            conn.execute(complaints_table.insert().values(
                complaint_id=cid, source="social_media", store="Westfield", date=now_iso,
                customer_text="bad service", source_table="raw_social_media",
                source_row_id=i, created_at=now_iso,
            ))
            conn.execute(classification_table.insert().values(
                complaint_id=cid, category="Service Quality", sentiment="Negative",
                severity="Medium", department="Operations", confidence=0.9,
                reasoning="test", model_used="test", classified_at=now_iso,
            ))
    fired = check_volume_spikes()
    assert any(a["details"]["store"] == "Westfield" and a["details"]["category"] == "Service Quality" for a in fired)


def test_live_panel_helpers_do_not_crash():
    from dashboard.live_panel import _reflect
    engine = db.get_engine()
    table = _reflect(engine, config.ALERTS_TABLE)
    assert table is not None


if __name__ == "__main__":
    setup_module()
    test_webhook_lands_and_fires_keyword_alert()
    test_webhook_rejects_bad_secret()
    test_polling_connector_lands_records()
    test_promotion_moves_landing_to_complaints_and_flips_is_processed()
    test_promotion_is_idempotent_for_replayed_source_records()
    test_batch_classifier_uses_the_actual_agent_interface()
    test_volume_spike_alert_fires()
    test_live_panel_helpers_do_not_crash()
    print("\nAll Phase 6 tests passed.")
