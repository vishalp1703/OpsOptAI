"""
utils.py
========
Shared helpers used by every source parser: robust date parsing,
deterministic ID generation, and text field cleanup. Centralizing
these means every source is cleaned the same way, which matters a
lot once this data starts feeding LLM agents downstream.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Optional

import pandas as pd
from dateutil import parser as dateutil_parser

logger = logging.getLogger("opspilot.ingestion")


def parse_date_safe(value) -> Optional[date]:
    """
    Attempt to parse a wide variety of real-world date formats
    (MM/DD/YYYY, DD-MM-YYYY, ISO timestamps, Excel serial dates, etc.)
    into a plain date. Returns None (never raises) if parsing fails,
    so a single bad date never crashes the whole ingestion run.
    """
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, pd.Timestamp):
        if pd.isna(value):
            return None
        return value.date()

    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "nat", ""}:
        return None

    try:
        return dateutil_parser.parse(text, fuzzy=True).date()
    except (ValueError, OverflowError, TypeError):
        logger.warning("Could not parse date value: %r", value)
        return None


def clean_str(value, default: str = "") -> str:
    """Trim whitespace, collapse internal whitespace/newlines, handle NaN/None."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return default
    text = str(value).strip()
    if text.lower() in {"nan", "none", "nat"}:
        return default
    return " ".join(text.split())


def make_complaint_id(source_prefix: str, raw_id, fallback_index: int) -> str:
    """
    Build a globally-unique, source-prefixed complaint ID.
    Falls back to a row-index-based ID if the raw source ID is missing,
    so ingestion never silently drops rows with blank IDs.
    """
    raw_id_clean = clean_str(raw_id)
    if raw_id_clean:
        return f"{source_prefix}-{raw_id_clean}"
    return f"{source_prefix}-ROW{fallback_index:06d}"


def combine_text(*parts: str, separator: str = " | ") -> str:
    """Join non-empty text parts (e.g. subject + description) into one field."""
    cleaned = [p.strip() for p in parts if p and p.strip() and p.strip().lower() != "nan"]
    return separator.join(cleaned)
