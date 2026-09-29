"""
Phase 6 configuration — matches your REAL DB schema, confirmed via
`python dump_schema.py` against C:\\Projects\\Project_2026\\OpsOptAI\\data\\opspilot.db
on 2026-08-14.

Your raw landing tables already existed (Phase 4 built them dormant) with
per-source TYPED columns, not a generic JSON blob — e.g. raw_pos_logs has
log_id, store_id, transaction_date, notes, etc. `is_processed` is your
promotion flag.

Your `complaints` table has NO ai fields — classification lives entirely in
the separate `complaint_classification` table, joined by complaint_id. Phase
6 respects that split: promotion writes only to `complaints`; batch
classification writes only to `complaint_classification`.

If your schema changes again, this is still the only file you should need
to edit — re-run `python -m workflows.realtime.schema_check` after any edit
to confirm.
"""

import os
from enum import Enum


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///data/opspilot.db")


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------
class IngestMode(str, Enum):
    POLL = "poll"
    WEBHOOK = "webhook"


# Per source:
#   landing_table   - your real raw_* table name
#   id_column       - the source's own business-key column (e.g. log_id)
#   id_prefix       - prefix used when Phase 6 needs to generate one
#   store_column    - real column holding store/account identity
#   date_column     - real column holding the source's own date
#   text_column     - real column holding the free-text body
#   extra_columns   - {logical_key: real_column_name} for source-specific
#                      fields worth capturing (same name on both sides here,
#                      kept explicit so it's obvious what's covered)
SOURCES = {
    "pos_logs": {
        "mode": IngestMode.POLL,
        "landing_table": "raw_pos_logs",
        "poll_interval_seconds": 300,
        "id_column": "log_id", "id_prefix": "POS",
        "store_column": "store_id", "date_column": "transaction_date", "text_column": "notes",
        "extra_columns": {"exception_type": "exception_type", "amount": "amount", "cashier_id": "cashier_id"},
    },
    "crm_notes": {
        "mode": IngestMode.POLL,
        "landing_table": "raw_crm_notes",
        "poll_interval_seconds": 600,
        "id_column": "note_id", "id_prefix": "CRM",
        "store_column": "account", "date_column": "note_date", "text_column": "note_text",
        "extra_columns": {"rep_name": "rep_name"},
    },
    "employee_feedback": {
        "mode": IngestMode.POLL,
        "landing_table": "raw_employee_feedback",
        "poll_interval_seconds": 1800,
        "id_column": "feedback_id", "id_prefix": "EMP",
        "store_column": "store", "date_column": "submission_date", "text_column": "feedback_text",
        "extra_columns": {"employee_role": "employee_role"},
    },
    "surveys": {
        "mode": IngestMode.POLL,
        "landing_table": "raw_surveys",
        "poll_interval_seconds": 1800,
        "id_column": "survey_id", "id_prefix": "SVY",
        "store_column": "store", "date_column": "response_date", "text_column": "comments",
        "extra_columns": {"nps_score": "nps_score"},
    },
    "customer_reviews": {
        "mode": IngestMode.WEBHOOK,
        "landing_table": "raw_customer_reviews",
        "webhook_path": "/webhooks/customer_reviews",
        "id_column": "review_id", "id_prefix": "REV",
        "store_column": "store_location", "date_column": "review_date", "text_column": "review_text",
        "extra_columns": {"rating": "rating", "platform": "platform"},
    },
    "support_tickets": {
        "mode": IngestMode.WEBHOOK,
        "landing_table": "raw_support_tickets",
        "webhook_path": "/webhooks/support_tickets",
        "id_column": "ticket_id", "id_prefix": "TIX",
        "store_column": "store", "date_column": "created_date", "text_column": "description",
        "extra_columns": {"priority": "priority", "subject": "subject", "status": "status"},
    },
    "social_media": {
        "mode": IngestMode.WEBHOOK,
        "landing_table": "raw_social_media",
        "webhook_path": "/webhooks/social_media",
        "id_column": "post_id", "id_prefix": "SOC",
        "store_column": "store_mentioned", "date_column": "post_date", "text_column": "text",
        "extra_columns": {"platform": "platform", "likes": "likes"},
    },
    "incidents": {
        "mode": IngestMode.WEBHOOK,
        "landing_table": "raw_incident_reports",
        "webhook_path": "/webhooks/incidents",
        "id_column": "incident_id", "id_prefix": "INC",
        "store_column": "store", "date_column": "incident_date", "text_column": "description",
        # NOTE: raw_incident_reports has its own `severity`/`category` columns, filled at
        # ingestion by the source system itself — distinct from complaint_classification's
        # AI-assigned severity/category, which get set later by the batch classifier.
        "extra_columns": {"severity": "severity", "category": "category"},
    },
}

# Columns present on every raw_* table regardless of source.
COMMON_LANDING_COLUMNS = {
    "auto_id": "id",              # autoincrement PK — never set manually
    "ingested_at": "ingested_at", # VARCHAR(40) ISO timestamp string (not a native DATETIME in your schema)
    "is_processed": "is_processed",
}

# Your real complaints table — no AI fields, see module docstring.
COMPLAINTS_TABLE = "complaints"
COMPLAINTS_COLUMNS = [
    "complaint_id", "source", "store", "date", "customer_text",
    "source_table", "source_row_id", "created_at",
]

# Your real classification table — one row per classified complaint.
CLASSIFICATION_TABLE = "complaint_classification"
CLASSIFICATION_COLUMNS = [
    "complaint_id", "category", "sentiment", "severity", "department",
    "confidence", "reasoning", "model_used", "classified_at",
]

ALERTS_TABLE = "alerts"  # confirmed [OK] via schema_check — created by data/init_alerts_table.py


# ---------------------------------------------------------------------------
# Webhook auth
# ---------------------------------------------------------------------------
WEBHOOK_SHARED_SECRET = os.getenv("WEBHOOK_SHARED_SECRET", "dev-secret-change-me")
WEBHOOK_HEADER_NAME = "X-OpsPilot-Webhook-Secret"


# ---------------------------------------------------------------------------
# Batch classification (Phase 2 hookup)
# ---------------------------------------------------------------------------
BATCH_CLASSIFY_INTERVAL_SECONDS = int(os.getenv("BATCH_CLASSIFY_INTERVAL_SECONDS", 900))  # 15 min
BATCH_CLASSIFY_MAX_ROWS_PER_RUN = int(os.getenv("BATCH_CLASSIFY_MAX_ROWS_PER_RUN", 50))


# ---------------------------------------------------------------------------
# Alerting
# ---------------------------------------------------------------------------
IMMEDIATE_ALERT_KEYWORDS = {
    "incidents": ["fire", "injury", "injured", "evacuat", "hospital", "assault", "lawsuit"],
    "social_media": ["viral", "boycott", "lawsuit", "health department", "foodborne"],
    "customer_reviews": ["food poisoning", "hospitalized", "lawsuit", "discrimination"],
}

SEVERITY_ALERT_THRESHOLD = "High"  # matches complaint_classification.severity values

VOLUME_SPIKE_THRESHOLD = 5
VOLUME_SPIKE_WINDOW_MINUTES = 60

SMTP_HOST = os.getenv("SMTP_HOST")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")
ALERT_EMAIL_FROM = os.getenv("ALERT_EMAIL_FROM", "alerts@opspilot.local")
ALERT_EMAIL_TO = [addr.strip() for addr in os.getenv("ALERT_EMAIL_TO", "").split(",") if addr.strip()]
