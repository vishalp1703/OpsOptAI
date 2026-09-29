"""
data/repository.py

Thin persistence layer between agents/schemas.py (Pydantic, what the
agents actually produce) and data/models.py (SQLAlchemy, how it's stored).

Why this exists: Phases 2/3 agents should be able to call
`save_classification(db, result)` and be done -- they shouldn't need to
know column names, FK ordering, or which fields are JSON vs plain columns.
That mapping knowledge lives here, once.

Every save_* function:
  - Accepts an open Session (caller controls the transaction/commit --
    typically via `with session_scope() as db:` from data/database.py)
  - Accepts the corresponding Pydantic model from agents/schemas.py
  - Upserts (SQLAlchemy merge) rather than blind-inserts, so re-running an
    agent stage on the same complaint_id/pattern_id/etc. updates the
    existing row instead of raising a duplicate-PK error
  - Returns the ORM instance that was written, in case the caller wants it

None of this executes an LLM call or does any business logic -- it is pure
plumbing. Agents in agents/*.py import from here; this module never
imports from agents/*.py (only from agents/schemas.py, for types).
"""

from __future__ import annotations

from typing import Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from agents.schemas import (
    ClassifiedComplaint,
    ComplaintInput,
    ExecutiveSummary as ExecutiveSummarySchema,
    JiraStory as JiraStorySchema,
    Pattern as PatternSchema,
    Recommendation as RecommendationSchema,
    RootCausedComplaint,
)
from data.models import (
    Complaint,
    ComplaintClassification,
    ExecutiveSummary,
    JiraStory,
    Pattern,
    Recommendation,
    RootCause,
)


# ---------------------------------------------------------------------------
# Complaints (Phase 1 output -> master table)
# ---------------------------------------------------------------------------

def save_complaint(
    db: Session,
    complaint: ComplaintInput,
    source_table: Optional[str] = None,
    source_row_id: Optional[int] = None,
) -> Complaint:
    """Insert/update one normalized complaint row from Phase 1's cleaned output."""
    row = Complaint(
        complaint_id=complaint.complaint_id,
        source=complaint.source,
        store=complaint.store,
        date=complaint.date,
        customer_text=complaint.customer_text,
        source_table=source_table,
        source_row_id=source_row_id,
    )
    return db.merge(row)


def get_complaint(db: Session, complaint_id: str) -> Optional[Complaint]:
    return db.get(Complaint, complaint_id)


def list_complaints(
    db: Session, store: Optional[str] = None, limit: int = 100, offset: int = 0
) -> Sequence[Complaint]:
    stmt = select(Complaint)
    if store:
        stmt = stmt.where(Complaint.store == store)
    stmt = stmt.limit(limit).offset(offset)
    return db.execute(stmt).scalars().all()


# ---------------------------------------------------------------------------
# Classification (Agent 1 / Phase 2)
# ---------------------------------------------------------------------------

def save_classification(db: Session, classified: ClassifiedComplaint) -> ComplaintClassification:
    """
    Persists Agent 1's output. Assumes the parent Complaint row already
    exists (call save_complaint first, or ensure Phase 1 already did).
    """
    row = ComplaintClassification(
        complaint_id=classified.complaint_id,
        category=classified.category,
        sentiment=classified.sentiment,
        severity=classified.severity,
        department=classified.department,
        confidence=classified.confidence,
        model_used=classified.model_used,
        classified_at=classified.classified_at,
    )
    return db.merge(row)


# ---------------------------------------------------------------------------
# Root Cause (Agent 2 / Phase 3)
# ---------------------------------------------------------------------------

def save_root_cause(db: Session, root_caused: RootCausedComplaint) -> RootCause:
    """Persists Agent 2's output (expects RootCausedComplaint, the full stitched row)."""
    row = RootCause(
        complaint_id=root_caused.complaint_id,
        root_cause_category=root_caused.root_cause_category,
        root_cause_explanation=root_caused.root_cause_explanation,
        contributing_factors=root_caused.contributing_factors,
        confidence=root_caused.root_cause_confidence,
        analyzed_at=None,  # not tracked on RootCausedComploint; set from RootCauseResult upstream if needed
    )
    return db.merge(row)


# ---------------------------------------------------------------------------
# Patterns (Agent 3 / Phase 3)
# ---------------------------------------------------------------------------

def save_pattern(db: Session, pattern: PatternSchema) -> Pattern:
    """
    Persists Agent 3's output, including wiring up the pattern_complaints
    many-to-many relationship from pattern.complaint_ids. Every ID in
    complaint_ids must already exist in the complaints table (Phase 1
    output) -- silently skips any that don't, rather than failing the
    whole pattern, and logs which ones were skipped.
    """
    row = Pattern(
        pattern_id=pattern.pattern_id,
        title=pattern.title,
        description=pattern.description,
        trend=pattern.trend,
        root_cause_category=pattern.root_cause_category,
        department=pattern.department,
        category=pattern.category,
        stores_affected=pattern.stores_affected,
        frequency=pattern.frequency,
        severity_breakdown=pattern.severity_breakdown,
        avg_confidence=pattern.avg_confidence,
        model_used=pattern.model_used,
        detected_at=pattern.detected_at,
    )
    row = db.merge(row)

    if pattern.complaint_ids:
        found = db.execute(
            select(Complaint).where(Complaint.complaint_id.in_(pattern.complaint_ids))
        ).scalars().all()
        missing = set(pattern.complaint_ids) - {c.complaint_id for c in found}
        if missing:
            print(f"⚠️  save_pattern({pattern.pattern_id}): {len(missing)} complaint_id(s) not found, skipped: {sorted(missing)[:5]}...")
        row.complaints = found

    return row


# ---------------------------------------------------------------------------
# Recommendations (Agent 4 / Phase 3)
# ---------------------------------------------------------------------------

def save_recommendation(db: Session, recommendation: RecommendationSchema) -> Recommendation:
    row = Recommendation(
        recommendation_id=recommendation.recommendation_id,
        pattern_id=recommendation.pattern_id,
        recommendation_type=recommendation.recommendation_type,
        title=recommendation.title,
        description=recommendation.description,
        expected_impact=recommendation.expected_impact,
        effort_estimate=recommendation.effort_estimate,
        owning_department=recommendation.owning_department,
        supporting_evidence=recommendation.supporting_evidence,
        confidence=recommendation.confidence,
        model_used=recommendation.model_used,
        generated_at=recommendation.generated_at,
    )
    return db.merge(row)


# ---------------------------------------------------------------------------
# Jira Stories (Agent 5 / Phase 3)
# ---------------------------------------------------------------------------

def save_jira_story(db: Session, story: JiraStorySchema) -> JiraStory:
    row = JiraStory(
        story_id=story.story_id,
        recommendation_id=story.recommendation_id,
        title=story.title,
        description=story.description,
        acceptance_criteria=story.acceptance_criteria,
        priority=story.priority,
        labels=story.labels,
        story_points_estimate=story.story_points_estimate,
        model_used=story.model_used,
        generated_at=story.generated_at,
    )
    return db.merge(row)


# ---------------------------------------------------------------------------
# Executive Summary (Agent 6 / Phase 3)
# ---------------------------------------------------------------------------

def save_executive_summary(db: Session, summary: ExecutiveSummarySchema) -> ExecutiveSummary:
    row = ExecutiveSummary(
        summary_id=summary.summary_id,
        generated_at=summary.generated_at,
        period_covered=summary.period_covered,
        total_complaints_analyzed=summary.total_complaints_analyzed,
        total_patterns_detected=summary.total_patterns_detected,
        total_recommendations=summary.total_recommendations,
        headline=summary.headline,
        key_findings=summary.key_findings,
        top_risk_areas=summary.top_risk_areas,
        narrative=summary.narrative,
        model_used=summary.model_used,
    )
    return db.merge(row)
