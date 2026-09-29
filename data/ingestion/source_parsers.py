"""
source_parsers.py
==================
One normalization function per data source. Each function takes a raw
pandas DataFrame (as read straight from a CSV/Excel export) and returns
a clean DataFrame with exactly the STANDARD_COLUMNS from schema.py.

Design notes:
- Column names in real-world exports vary ("Store", "store_id", "Location").
  `find_column()` matches candidates case-/whitespace-insensitively so
  parsers don't break the moment a source system renames a column.
- Every row is validated through the StandardComplaint pydantic model.
  Rows that fail validation (e.g. totally empty text) are skipped and
  logged, never silently corrupted and never crashing the whole batch.
- AI-derived fields (sentiment, category, root_cause, etc.) are left at
  their schema defaults here — that's Phase 2+'s job.
"""

from __future__ import annotations

import logging
from typing import Callable, Iterable, List, Optional

import pandas as pd
from pydantic import ValidationError

from agents.ingestion_mapping_agent import DEFAULT_INGESTION_MAPPING_AGENT
from data.ingestion.schema import STANDARD_COLUMNS, SourceType
from data.ingestion.utils import clean_str, combine_text, make_complaint_id, parse_date_safe

logger = logging.getLogger("opspilot.ingestion")


def find_column(df: pd.DataFrame, candidates: Iterable[str]) -> Optional[str]:
    """Return the actual df column name matching one of `candidates` (case/space-insensitive)."""
    normalized = {c.strip().lower().replace(" ", "").replace("_", ""): c for c in df.columns}
    for candidate in candidates:
        key = candidate.strip().lower().replace(" ", "").replace("_", "")
        if key in normalized:
            return normalized[key]
    return None


def _build_records(
    df: pd.DataFrame,
    source: SourceType,
    row_to_dict: Callable[[pd.Series, int], dict],
) -> pd.DataFrame:
    """
    Shared row-by-row builder: applies `row_to_dict` to every row, validates
    through StandardComplaint, and returns a DataFrame of only valid rows.
    """
    records: List[dict] = []
    skipped = 0

    for idx, row in df.iterrows():
        try:
            payload = row_to_dict(row, idx)
            mapped = DEFAULT_INGESTION_MAPPING_AGENT.normalize(payload, source)
            records.append(mapped.complaint.model_dump())
        except ValidationError as e:
            skipped += 1
            logger.warning("Skipping invalid %s row %s: %s", source.value, idx, e.errors()[0]["msg"])
        except Exception as e:  # defensive: one bad row should never kill the run
            skipped += 1
            logger.warning("Skipping malformed %s row %s: %s", source.value, idx, e)

    if skipped:
        logger.info("%s: skipped %d/%d unparseable rows", source.value, skipped, len(df))

    if not records:
        return pd.DataFrame(columns=STANDARD_COLUMNS)

    result = pd.DataFrame(records)
    # Deduplicate on complaint_id within this source (keep first occurrence).
    before = len(result)
    result = result.drop_duplicates(subset="complaint_id", keep="first")
    if len(result) < before:
        logger.info("%s: dropped %d duplicate complaint_id rows", source.value, before - len(result))

    return result[STANDARD_COLUMNS]


# ---------------------------------------------------------------------------
# 1. Customer Reviews  (Google/Yelp/App-store style star ratings + text)
# ---------------------------------------------------------------------------
def parse_customer_reviews(df: pd.DataFrame) -> pd.DataFrame:
    col_id = find_column(df, ["review_id", "id"])
    col_store = find_column(df, ["store_location", "store", "location"])
    col_date = find_column(df, ["review_date", "date"])
    col_text = find_column(df, ["review_text", "text", "comment"])
    col_rating = find_column(df, ["rating", "stars"])
    col_platform = find_column(df, ["platform", "source_platform"])

    def to_dict(row, idx):
        rating = clean_str(row.get(col_rating)) if col_rating else ""
        platform = clean_str(row.get(col_platform)) if col_platform else ""
        text = clean_str(row.get(col_text)) if col_text else ""
        text_with_context = combine_text(
            f"[{rating}-star review{' via ' + platform if platform else ''}]" if rating else "",
            text,
        )
        return dict(
            complaint_id=make_complaint_id("REVIEW", row.get(col_id) if col_id else None, idx),
            source=SourceType.CUSTOMER_REVIEW,
            store=clean_str(row.get(col_store), default="UNKNOWN") if col_store else "UNKNOWN",
            date=parse_date_safe(row.get(col_date)) if col_date else None,
            customer_text=text_with_context,
        )

    return _build_records(df, SourceType.CUSTOMER_REVIEW, to_dict)


# ---------------------------------------------------------------------------
# 2. Support Tickets
# ---------------------------------------------------------------------------
def parse_support_tickets(df: pd.DataFrame) -> pd.DataFrame:
    col_id = find_column(df, ["ticket_id", "id"])
    col_store = find_column(df, ["store", "location", "store_id"])
    col_date = find_column(df, ["created_date", "date", "opened_date"])
    col_subject = find_column(df, ["subject", "title"])
    col_desc = find_column(df, ["description", "details", "body"])
    col_priority = find_column(df, ["priority"])

    def to_dict(row, idx):
        priority = clean_str(row.get(col_priority)) if col_priority else ""
        text = combine_text(
            clean_str(row.get(col_subject)) if col_subject else "",
            clean_str(row.get(col_desc)) if col_desc else "",
        )
        if priority:
            text = combine_text(f"[priority: {priority}]", text)
        return dict(
            complaint_id=make_complaint_id("TICKET", row.get(col_id) if col_id else None, idx),
            source=SourceType.SUPPORT_TICKET,
            store=clean_str(row.get(col_store), default="UNKNOWN") if col_store else "UNKNOWN",
            date=parse_date_safe(row.get(col_date)) if col_date else None,
            customer_text=text,
        )

    return _build_records(df, SourceType.SUPPORT_TICKET, to_dict)


# ---------------------------------------------------------------------------
# 3. POS Logs (exception/incident entries logged at register: voids,
#    refunds, discounts — the subset of POS activity that carries a
#    human-written note, which is what's useful for root-cause analysis)
# ---------------------------------------------------------------------------
def parse_pos_logs(df: pd.DataFrame) -> pd.DataFrame:
    col_id = find_column(df, ["log_id", "id"])
    col_store = find_column(df, ["store_id", "store", "location"])
    col_date = find_column(df, ["transaction_date", "date"])
    col_exc_type = find_column(df, ["exception_type", "type"])
    col_amount = find_column(df, ["amount"])
    col_notes = find_column(df, ["notes", "note", "reason"])

    def to_dict(row, idx):
        exc_type = clean_str(row.get(col_exc_type)) if col_exc_type else ""
        amount = clean_str(row.get(col_amount)) if col_amount else ""
        notes = clean_str(row.get(col_notes)) if col_notes else ""
        text = combine_text(
            f"[{exc_type}{' $' + amount if amount else ''}]" if exc_type else "",
            notes,
        )
        return dict(
            complaint_id=make_complaint_id("POS", row.get(col_id) if col_id else None, idx),
            source=SourceType.POS_LOG,
            store=clean_str(row.get(col_store), default="UNKNOWN") if col_store else "UNKNOWN",
            date=parse_date_safe(row.get(col_date)) if col_date else None,
            customer_text=text,
        )

    return _build_records(df, SourceType.POS_LOG, to_dict)


# ---------------------------------------------------------------------------
# 4. Employee Feedback
# ---------------------------------------------------------------------------
def parse_employee_feedback(df: pd.DataFrame) -> pd.DataFrame:
    col_id = find_column(df, ["feedback_id", "id"])
    col_store = find_column(df, ["store", "location"])
    col_date = find_column(df, ["submission_date", "date"])
    col_text = find_column(df, ["feedback_text", "text", "comment"])
    col_role = find_column(df, ["employee_role", "role"])

    def to_dict(row, idx):
        role = clean_str(row.get(col_role)) if col_role else ""
        text = clean_str(row.get(col_text)) if col_text else ""
        text_with_context = combine_text(f"[role: {role}]" if role else "", text)
        return dict(
            complaint_id=make_complaint_id("EMP", row.get(col_id) if col_id else None, idx),
            source=SourceType.EMPLOYEE_FEEDBACK,
            store=clean_str(row.get(col_store), default="UNKNOWN") if col_store else "UNKNOWN",
            date=parse_date_safe(row.get(col_date)) if col_date else None,
            customer_text=text_with_context,
        )

    return _build_records(df, SourceType.EMPLOYEE_FEEDBACK, to_dict)


# ---------------------------------------------------------------------------
# 5. Surveys (e.g. NPS-style: numeric score + open comment)
# ---------------------------------------------------------------------------
def parse_surveys(df: pd.DataFrame) -> pd.DataFrame:
    col_id = find_column(df, ["survey_id", "id"])
    col_store = find_column(df, ["store", "location"])
    col_date = find_column(df, ["response_date", "date"])
    col_score = find_column(df, ["nps_score", "score"])
    col_comments = find_column(df, ["comments", "comment", "feedback"])

    def to_dict(row, idx):
        score = clean_str(row.get(col_score)) if col_score else ""
        comments = clean_str(row.get(col_comments)) if col_comments else ""
        text = combine_text(f"[NPS: {score}]" if score else "", comments)
        return dict(
            complaint_id=make_complaint_id("SURVEY", row.get(col_id) if col_id else None, idx),
            source=SourceType.SURVEY,
            store=clean_str(row.get(col_store), default="UNKNOWN") if col_store else "UNKNOWN",
            date=parse_date_safe(row.get(col_date)) if col_date else None,
            customer_text=text,
        )

    return _build_records(df, SourceType.SURVEY, to_dict)


# ---------------------------------------------------------------------------
# 6. Social Media
# ---------------------------------------------------------------------------
def parse_social_media(df: pd.DataFrame) -> pd.DataFrame:
    col_id = find_column(df, ["post_id", "id"])
    col_store = find_column(df, ["store_mentioned", "store", "location"])
    col_date = find_column(df, ["post_date", "date"])
    col_text = find_column(df, ["text", "post_text", "content"])
    col_platform = find_column(df, ["platform"])

    def to_dict(row, idx):
        platform = clean_str(row.get(col_platform)) if col_platform else ""
        text = clean_str(row.get(col_text)) if col_text else ""
        text_with_context = combine_text(f"[{platform} post]" if platform else "", text)
        return dict(
            complaint_id=make_complaint_id("SOCIAL", row.get(col_id) if col_id else None, idx),
            source=SourceType.SOCIAL_MEDIA,
            store=clean_str(row.get(col_store), default="UNKNOWN") if col_store else "UNKNOWN",
            date=parse_date_safe(row.get(col_date)) if col_date else None,
            customer_text=text_with_context,
        )

    return _build_records(df, SourceType.SOCIAL_MEDIA, to_dict)


# ---------------------------------------------------------------------------
# 7. CRM Notes
# ---------------------------------------------------------------------------
def parse_crm_notes(df: pd.DataFrame) -> pd.DataFrame:
    col_id = find_column(df, ["note_id", "id"])
    col_store = find_column(df, ["store", "account", "location"])
    col_date = find_column(df, ["note_date", "date"])
    col_text = find_column(df, ["note_text", "notes", "text"])
    col_rep = find_column(df, ["rep_name", "rep"])

    def to_dict(row, idx):
        rep = clean_str(row.get(col_rep)) if col_rep else ""
        text = clean_str(row.get(col_text)) if col_text else ""
        text_with_context = combine_text(f"[rep: {rep}]" if rep else "", text)
        return dict(
            complaint_id=make_complaint_id("CRM", row.get(col_id) if col_id else None, idx),
            source=SourceType.CRM_NOTE,
            store=clean_str(row.get(col_store), default="UNKNOWN") if col_store else "UNKNOWN",
            date=parse_date_safe(row.get(col_date)) if col_date else None,
            customer_text=text_with_context,
        )

    return _build_records(df, SourceType.CRM_NOTE, to_dict)


# ---------------------------------------------------------------------------
# 8. Incident Reports
# ---------------------------------------------------------------------------
def parse_incident_reports(df: pd.DataFrame) -> pd.DataFrame:
    col_id = find_column(df, ["incident_id", "id"])
    col_store = find_column(df, ["store", "location"])
    col_date = find_column(df, ["incident_date", "date"])
    col_desc = find_column(df, ["description", "details"])
    col_severity = find_column(df, ["severity"])
    col_category = find_column(df, ["category", "type"])

    def to_dict(row, idx):
        severity = clean_str(row.get(col_severity)) if col_severity else ""
        category = clean_str(row.get(col_category)) if col_category else ""
        desc = clean_str(row.get(col_desc)) if col_desc else ""
        tag = " ".join(t for t in [f"severity:{severity}" if severity else "", category] if t)
        text = combine_text(f"[{tag}]" if tag else "", desc)
        return dict(
            complaint_id=make_complaint_id("INCIDENT", row.get(col_id) if col_id else None, idx),
            source=SourceType.INCIDENT_REPORT,
            store=clean_str(row.get(col_store), default="UNKNOWN") if col_store else "UNKNOWN",
            date=parse_date_safe(row.get(col_date)) if col_date else None,
            customer_text=text,
        )

    return _build_records(df, SourceType.INCIDENT_REPORT, to_dict)


# Registry mapping a source key (used as the raw filename stem) to its parser.
# app.py / pipeline.py use this to dispatch each raw file to the right function.
SOURCE_PARSERS: dict[str, Callable[[pd.DataFrame], pd.DataFrame]] = {
    "customer_reviews": parse_customer_reviews,
    "support_tickets": parse_support_tickets,
    "pos_logs": parse_pos_logs,
    "employee_feedback": parse_employee_feedback,
    "surveys": parse_surveys,
    "social_media": parse_social_media,
    "crm_notes": parse_crm_notes,
    "incident_reports": parse_incident_reports,
}
