"""
schema.py
=========
Defines the STANDARD COMPLAINT SCHEMA that every raw record from every
source (customer reviews, support tickets, POS logs, employee feedback,
surveys, social media, CRM notes, incident reports) gets normalized into.

This is the single contract the rest of OpsPilot AI (agents, workflows,
storage, dashboard) is built against. Phase 1 populates the "known"
fields (complaint_id, source, store, date, customer_text). The
AI-derived fields (sentiment, category, root_cause, department,
severity, confidence, recommendation) are left blank here and filled
in by later phases (Agent 1 classification, Agent 2 root cause, etc.).

Keeping this in one place means Phase 2+ agents can `from
data.ingestion.schema import StandardComplaint` and know exactly what
shape of data they're working with.
"""

from __future__ import annotations

from datetime import date as date_type
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class SourceType(str, Enum):
    """Canonical source identifiers. Keep in sync with source_parsers.py."""

    CUSTOMER_REVIEW = "customer_review"
    SUPPORT_TICKET = "support_ticket"
    POS_LOG = "pos_log"
    EMPLOYEE_FEEDBACK = "employee_feedback"
    SURVEY = "survey"
    SOCIAL_MEDIA = "social_media"
    CRM_NOTE = "crm_note"
    INCIDENT_REPORT = "incident_report"


class StandardComplaint(BaseModel):
    """
    The normalized record every raw row is converted into.

    Fields populated in Phase 1 (ingestion):
        complaint_id, source, store, date, customer_text

    Fields left as defaults in Phase 1, populated by later agents:
        sentiment, category, root_cause, department, severity,
        confidence, recommendation
    """

    complaint_id: str = Field(..., description="Globally unique ID, prefixed by source")
    source: SourceType
    store: str = Field(..., description="Store/location identifier; 'UNKNOWN' if not present in raw data")
    date: Optional[date_type] = Field(None, description="Normalized ISO date; None if unparseable/missing")
    customer_text: str = Field(..., description="The free-text content that will be sent to the LLM in Phase 2")

    # --- Populated by later phases; default to empty/neutral values in Phase 1 ---
    sentiment: str = ""
    category: str = ""
    root_cause: str = ""
    department: str = ""
    severity: str = ""
    confidence: float = 0.0
    recommendation: str = ""

    @field_validator("complaint_id", "store")
    @classmethod
    def strip_and_require_nonempty(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("must be a non-empty string")
        return v

    @field_validator("customer_text")
    @classmethod
    def clean_text(cls, v: str) -> str:
        # Collapse whitespace/newlines that commonly leak in from CSV/Excel exports.
        cleaned = " ".join((v or "").split()).strip()
        if not cleaned:
            raise ValueError("must contain complaint text")
        return cleaned

    model_config = {
        "use_enum_values": True,
    }


# Columns written to the cleaned CSV outputs, in a fixed, stable order.
STANDARD_COLUMNS = [
    "complaint_id",
    "source",
    "store",
    "date",
    "customer_text",
    "sentiment",
    "category",
    "root_cause",
    "department",
    "severity",
    "confidence",
    "recommendation",
]
