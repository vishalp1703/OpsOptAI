"""
data/models.py

SQLAlchemy ORM models for OpsPilot AI's structured storage layer.

DESIGN PRINCIPLES
------------------
1. Two layers of tables:

   a) RAW SOURCE TABLES (one per ingestion source, 8 total) -- near-verbatim
      landing zone for whatever Phase 1 reads out of data/raw/*.csv. Column
      names match generate_sample_data.py exactly so Phase 1's loader can
      bulk-insert with minimal transformation. Messy/inconsistent data is
      expected here (that's the point of Phase 1's cleaning step existing).

   b) PIPELINE TABLES -- the normalized master Complaint plus one table per
      agent stage (Classification, RootCause, Pattern, Recommendation,
      JiraStory, ExecutiveSummary). These mirror agents/schemas.py Pydantic
      models field-for-field, so a later phase can do:
          row = ComplaintClassification(**classification_result.model_dump())
      with no manual field mapping. See data/repository.py for the actual
      convenience functions that do this.

2. Enums are imported directly from agents/schemas.py rather than
   redefined here. One source of truth: if a category/severity/department
   value is added there, it's automatically valid here too. Stored as
   native_enum=False (plain VARCHAR + CHECK constraint) rather than a
   Postgres-native ENUM type -- this keeps SQLite (dev) and Postgres (prod)
   behaving identically and avoids ALTER TYPE migration pain if a category
   list changes later.

3. List/dict-shaped fields (contributing_factors, stores_affected,
   severity_breakdown, supporting_evidence, acceptance_criteria, labels,
   key_findings, top_risk_areas) are stored as JSON columns. They're
   descriptive metadata, not things the dashboard needs to join across
   rows on -- SQLAlchemy's JSON type round-trips Python lists/dicts
   transparently on both SQLite and Postgres.

4. The one place real relational integrity matters is Pattern <-> Complaint
   (many-to-many: a pattern is detected from many complaints, and the
   dashboard needs to query "which complaints belong to pattern X" and
   "which pattern(s) is complaint Y part of" efficiently). That gets a
   proper association table (pattern_complaints), not a JSON blob.

5. Every pipeline table keeps model_used / a timestamp column, matching
   the metadata already tracked in the Pydantic schemas -- useful later
   for the model_evaluator and for the dashboard's "AI confidence score"
   panel (Phase 5).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    Column,
    Enum as SAEnum,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from agents.schemas import (
    Category,
    Department,
    ImpactLevel,
    RecommendationType,
    RootCauseCategory,
    Sentiment,
    Severity,
    StoryPriority,
    TrendDirection,
)
from data.database import Base


def _uuid() -> str:
    """Default PK generator for pipeline rows whose ID isn't source-supplied."""
    return uuid.uuid4().hex[:12]


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# Shorthand so every enum column below is declared the same way:
# stored as VARCHAR + CHECK constraint, not a Postgres-native ENUM type.
def _enum_col(enum_cls, **kwargs):
    return mapped_column(SAEnum(enum_cls, native_enum=False, validate_strings=True), **kwargs)


# ===========================================================================
# LAYER 1 -- RAW SOURCE TABLES (Phase 1 ingestion landing zone)
#
# Column names deliberately match data/generate_sample_data.py's CSV output
# 1:1 so the Phase 1 loader can do a near-direct pandas.to_sql() style bulk
# insert. Every raw row keeps its natural source ID as a plain string
# (NOT the primary key -- source systems can and do reuse/duplicate IDs,
# which generate_sample_data.py intentionally simulates) plus ingestion
# bookkeeping columns.
# ===========================================================================

class _RawBase:
    """Mixin: bookkeeping columns shared by every raw source table."""
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ingested_at: Mapped[str] = mapped_column(String(40), default=_utcnow, nullable=False)
    # Set True once Phase 1 has folded this row into the `complaints` table.
    is_processed: Mapped[bool] = mapped_column(default=False, nullable=False)


class CustomerReviewRaw(Base, _RawBase):
    __tablename__ = "raw_customer_reviews"

    review_id: Mapped[str] = mapped_column(String(20), index=True)
    store_location: Mapped[Optional[str]] = mapped_column(String(100))
    review_date: Mapped[Optional[str]] = mapped_column(String(30))  # raw, pre-normalization format
    rating: Mapped[Optional[int]] = mapped_column(Integer)
    review_text: Mapped[Optional[str]] = mapped_column(Text)
    platform: Mapped[Optional[str]] = mapped_column(String(30))


class SupportTicketRaw(Base, _RawBase):
    __tablename__ = "raw_support_tickets"

    ticket_id: Mapped[str] = mapped_column(String(20), index=True)
    store: Mapped[Optional[str]] = mapped_column(String(100))
    created_date: Mapped[Optional[str]] = mapped_column(String(30))
    priority: Mapped[Optional[str]] = mapped_column(String(20))
    subject: Mapped[Optional[str]] = mapped_column(String(255))
    description: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[Optional[str]] = mapped_column(String(20))


class POSLogRaw(Base, _RawBase):
    __tablename__ = "raw_pos_logs"

    log_id: Mapped[str] = mapped_column(String(20), index=True)
    store_id: Mapped[Optional[str]] = mapped_column(String(100))
    transaction_date: Mapped[Optional[str]] = mapped_column(String(30))
    exception_type: Mapped[Optional[str]] = mapped_column(String(30))
    amount: Mapped[Optional[float]] = mapped_column(Float)
    cashier_id: Mapped[Optional[str]] = mapped_column(String(20))
    notes: Mapped[Optional[str]] = mapped_column(Text)


class EmployeeFeedbackRaw(Base, _RawBase):
    __tablename__ = "raw_employee_feedback"

    feedback_id: Mapped[str] = mapped_column(String(20), index=True)
    store: Mapped[Optional[str]] = mapped_column(String(100))
    submission_date: Mapped[Optional[str]] = mapped_column(String(30))
    employee_role: Mapped[Optional[str]] = mapped_column(String(30))
    feedback_text: Mapped[Optional[str]] = mapped_column(Text)


class SurveyRaw(Base, _RawBase):
    __tablename__ = "raw_surveys"

    survey_id: Mapped[str] = mapped_column(String(20), index=True)
    store: Mapped[Optional[str]] = mapped_column(String(100))
    response_date: Mapped[Optional[str]] = mapped_column(String(30))
    nps_score: Mapped[Optional[int]] = mapped_column(Integer)
    comments: Mapped[Optional[str]] = mapped_column(Text)


class SocialMediaRaw(Base, _RawBase):
    __tablename__ = "raw_social_media"

    post_id: Mapped[str] = mapped_column(String(20), index=True)
    store_mentioned: Mapped[Optional[str]] = mapped_column(String(100))
    post_date: Mapped[Optional[str]] = mapped_column(String(30))
    platform: Mapped[Optional[str]] = mapped_column(String(30))
    text: Mapped[Optional[str]] = mapped_column(Text)
    likes: Mapped[Optional[int]] = mapped_column(Integer)


class CRMNoteRaw(Base, _RawBase):
    __tablename__ = "raw_crm_notes"

    note_id: Mapped[str] = mapped_column(String(20), index=True)
    account: Mapped[Optional[str]] = mapped_column(String(100))
    note_date: Mapped[Optional[str]] = mapped_column(String(30))
    rep_name: Mapped[Optional[str]] = mapped_column(String(120))
    note_text: Mapped[Optional[str]] = mapped_column(Text)


class IncidentReportRaw(Base, _RawBase):
    __tablename__ = "raw_incident_reports"

    incident_id: Mapped[str] = mapped_column(String(20), index=True)
    store: Mapped[Optional[str]] = mapped_column(String(100))
    incident_date: Mapped[Optional[str]] = mapped_column(String(30))
    severity: Mapped[Optional[str]] = mapped_column(String(20))
    category: Mapped[Optional[str]] = mapped_column(String(50))
    description: Mapped[Optional[str]] = mapped_column(Text)


# Convenience lookup used by data/init_db.py and Phase 1's loader to map
# a generate_sample_data.py source key -> its raw ORM table.
RAW_TABLE_REGISTRY: dict[str, type[Base]] = {
    "customer_reviews": CustomerReviewRaw,
    "support_tickets": SupportTicketRaw,
    "pos_logs": POSLogRaw,
    "employee_feedback": EmployeeFeedbackRaw,
    "surveys": SurveyRaw,
    "social_media": SocialMediaRaw,
    "crm_notes": CRMNoteRaw,
    "incident_reports": IncidentReportRaw,
}


# ===========================================================================
# LAYER 2 -- PIPELINE TABLES
# ===========================================================================

class Complaint(Base):
    """
    Normalized master record -- the output of Phase 1 cleaning, one row per
    complaint regardless of which raw source it came from. This is the
    parent every downstream agent stage hangs off of via complaint_id.

    Mirrors agents/schemas.py ComplaintInput plus lightweight provenance
    (source_table/source_row_id) so a complaint can always be traced back
    to its original raw row for auditing/debugging.
    """
    __tablename__ = "complaints"

    complaint_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    source: Mapped[Optional[str]] = mapped_column(String(50))
    store: Mapped[Optional[str]] = mapped_column(String(100), index=True)
    date: Mapped[Optional[str]] = mapped_column(String(30))  # normalized ISO date, set by Phase 1
    customer_text: Mapped[str] = mapped_column(Text, nullable=False)

    # Provenance back to the raw landing table, e.g. ("raw_customer_reviews", 42)
    source_table: Mapped[Optional[str]] = mapped_column(String(50))
    source_row_id: Mapped[Optional[int]] = mapped_column(Integer)

    created_at: Mapped[str] = mapped_column(String(40), default=_utcnow, nullable=False)

    # One-to-one children (nullable until that phase's agent has run)
    classification: Mapped[Optional["ComplaintClassification"]] = relationship(
        back_populates="complaint", uselist=False, cascade="all, delete-orphan"
    )
    root_cause: Mapped[Optional["RootCause"]] = relationship(
        back_populates="complaint", uselist=False, cascade="all, delete-orphan"
    )
    patterns: Mapped[list["Pattern"]] = relationship(
        secondary="pattern_complaints", back_populates="complaints"
    )


class ComplaintClassification(Base):
    """
    Agent 1 output. Mirrors agents.schemas.ClassifiedComplaint's
    classification-specific fields exactly (category, sentiment, severity,
    department, confidence, model_used, classified_at).

    One-to-one with Complaint via complaint_id (both PK and FK).
    """
    __tablename__ = "complaint_classification"

    complaint_id: Mapped[str] = mapped_column(
        String(20), ForeignKey("complaints.complaint_id", ondelete="CASCADE"), primary_key=True
    )
    category: Mapped[Category] = _enum_col(Category, nullable=False)
    sentiment: Mapped[Sentiment] = _enum_col(Sentiment, nullable=False)
    severity: Mapped[Severity] = _enum_col(Severity, nullable=False)
    department: Mapped[Department] = _enum_col(Department, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    reasoning: Mapped[Optional[str]] = mapped_column(String(320))
    model_used: Mapped[Optional[str]] = mapped_column(String(80))
    classified_at: Mapped[Optional[str]] = mapped_column(String(40))

    complaint: Mapped["Complaint"] = relationship(back_populates="classification")


class RootCause(Base):
    """
    Agent 2 output. Mirrors agents.schemas.RootCauseResult.
    One-to-one with Complaint via complaint_id.
    """
    __tablename__ = "root_causes"

    complaint_id: Mapped[str] = mapped_column(
        String(20), ForeignKey("complaints.complaint_id", ondelete="CASCADE"), primary_key=True
    )
    root_cause_category: Mapped[RootCauseCategory] = _enum_col(RootCauseCategory, nullable=False)
    root_cause_explanation: Mapped[str] = mapped_column(String(520), nullable=False)
    contributing_factors: Mapped[list] = mapped_column(JSON, default=list)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    model_used: Mapped[Optional[str]] = mapped_column(String(80))
    analyzed_at: Mapped[Optional[str]] = mapped_column(String(40))

    complaint: Mapped["Complaint"] = relationship(back_populates="root_cause")


# Many-to-many join table between patterns and the complaints that make
# them up. Plain Core Table (not a mapped class) since it carries no data
# of its own beyond the two FKs -- standard SQLAlchemy pattern for a pure
# association table.
pattern_complaints = Table(
    "pattern_complaints",
    Base.metadata,
    Column("pattern_id", String(20), ForeignKey("patterns.pattern_id", ondelete="CASCADE"), primary_key=True),
    Column("complaint_id", String(20), ForeignKey("complaints.complaint_id", ondelete="CASCADE"), primary_key=True),
)


class Pattern(Base):
    """
    Agent 3 output. Mirrors agents.schemas.Pattern.

    complaint_ids from the Pydantic model becomes a real many-to-many
    relationship (pattern_complaints) instead of a JSON list, so the
    dashboard can efficiently query "all complaints behind this pattern"
    or "which patterns is this complaint part of" with a join instead of
    deserializing JSON in Python. stores_affected/severity_breakdown stay
    as JSON since nothing needs to join on them.
    """
    __tablename__ = "patterns"

    pattern_id: Mapped[str] = mapped_column(String(20), primary_key=True, default=_uuid)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(String(620), nullable=False)
    trend: Mapped[TrendDirection] = _enum_col(TrendDirection, nullable=False)
    root_cause_category: Mapped[RootCauseCategory] = _enum_col(RootCauseCategory, nullable=False)
    department: Mapped[Department] = _enum_col(Department, nullable=False)
    category: Mapped[Category] = _enum_col(Category, nullable=False)
    stores_affected: Mapped[list] = mapped_column(JSON, default=list)
    frequency: Mapped[int] = mapped_column(Integer, nullable=False)
    severity_breakdown: Mapped[dict] = mapped_column(JSON, default=dict)
    avg_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    model_used: Mapped[Optional[str]] = mapped_column(String(80))
    detected_at: Mapped[Optional[str]] = mapped_column(String(40))

    complaints: Mapped[list["Complaint"]] = relationship(
        secondary=pattern_complaints, back_populates="patterns"
    )
    recommendations: Mapped[list["Recommendation"]] = relationship(
        back_populates="pattern", cascade="all, delete-orphan"
    )


class Recommendation(Base):
    """
    Agent 4 output. Mirrors agents.schemas.Recommendation.
    Many-to-one: several recommendations can come off of one pattern.
    """
    __tablename__ = "recommendations"

    recommendation_id: Mapped[str] = mapped_column(String(20), primary_key=True, default=_uuid)
    pattern_id: Mapped[str] = mapped_column(
        String(20), ForeignKey("patterns.pattern_id", ondelete="CASCADE"), nullable=False, index=True
    )
    recommendation_type: Mapped[RecommendationType] = _enum_col(RecommendationType, nullable=False)
    title: Mapped[str] = mapped_column(String(150), nullable=False)
    description: Mapped[str] = mapped_column(String(820), nullable=False)
    expected_impact: Mapped[ImpactLevel] = _enum_col(ImpactLevel, nullable=False)
    effort_estimate: Mapped[ImpactLevel] = _enum_col(ImpactLevel, nullable=False)
    owning_department: Mapped[Department] = _enum_col(Department, nullable=False)
    supporting_evidence: Mapped[list] = mapped_column(JSON, default=list)  # sample complaint_ids
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    model_used: Mapped[Optional[str]] = mapped_column(String(80))
    generated_at: Mapped[Optional[str]] = mapped_column(String(40))

    pattern: Mapped["Pattern"] = relationship(back_populates="recommendations")
    jira_story: Mapped[Optional["JiraStory"]] = relationship(
        back_populates="recommendation", uselist=False, cascade="all, delete-orphan"
    )


class JiraStory(Base):
    """
    Agent 5 output. Mirrors agents.schemas.JiraStory.
    One-to-one with Recommendation (each recommendation gets one story).
    """
    __tablename__ = "jira_stories"

    story_id: Mapped[str] = mapped_column(String(20), primary_key=True, default=_uuid)
    recommendation_id: Mapped[str] = mapped_column(
        String(20), ForeignKey("recommendations.recommendation_id", ondelete="CASCADE"),
        nullable=False, unique=True,
    )
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    acceptance_criteria: Mapped[list] = mapped_column(JSON, default=list)
    priority: Mapped[StoryPriority] = _enum_col(StoryPriority, nullable=False)
    labels: Mapped[list] = mapped_column(JSON, default=list)
    story_points_estimate: Mapped[Optional[int]] = mapped_column(Integer)
    model_used: Mapped[Optional[str]] = mapped_column(String(80))
    generated_at: Mapped[Optional[str]] = mapped_column(String(40))

    recommendation: Mapped["Recommendation"] = relationship(back_populates="jira_story")


class ExecutiveSummary(Base):
    """
    Agent 6 output. Mirrors agents.schemas.ExecutiveSummary. Standalone --
    one row per pipeline run, not FK'd to any single complaint/pattern
    since it summarizes across all of them for a given period.
    """
    __tablename__ = "executive_summaries"

    summary_id: Mapped[str] = mapped_column(String(20), primary_key=True, default=_uuid)
    generated_at: Mapped[str] = mapped_column(String(40), nullable=False)
    period_covered: Mapped[Optional[str]] = mapped_column(String(60))
    total_complaints_analyzed: Mapped[int] = mapped_column(Integer, nullable=False)
    total_patterns_detected: Mapped[int] = mapped_column(Integer, nullable=False)
    total_recommendations: Mapped[int] = mapped_column(Integer, nullable=False)
    headline: Mapped[str] = mapped_column(String(160), nullable=False)
    key_findings: Mapped[list] = mapped_column(JSON, default=list)
    top_risk_areas: Mapped[list] = mapped_column(JSON, default=list)
    narrative: Mapped[str] = mapped_column(Text, nullable=False)
    model_used: Mapped[Optional[str]] = mapped_column(String(80))


__all__ = [
    # raw
    "CustomerReviewRaw", "SupportTicketRaw", "POSLogRaw", "EmployeeFeedbackRaw",
    "SurveyRaw", "SocialMediaRaw", "CRMNoteRaw", "IncidentReportRaw",
    "RAW_TABLE_REGISTRY",
    # pipeline
    "Complaint", "ComplaintClassification", "RootCause",
    "Pattern", "pattern_complaints", "Recommendation", "JiraStory", "ExecutiveSummary",
]
