"""
agents/schemas.py

Pydantic data contracts used across the OpsPilot AI agent pipeline.

Phase 2 only produces the "classification" slice of the master complaint
schema (category, sentiment, severity, department, confidence). The
remaining fields (root_cause, recommendation) are populated by later
agents in Phase 3 and are intentionally left out of ClassificationResult.

Keeping these as Pydantic models (not raw dicts) gives us:
  - Automatic validation of LLM output against allowed enum values
  - A single source of truth other agents/phases can import
  - Free serialization to/from JSON for storage (Phase 4) and the
    dashboard (Phase 5)
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel as _BaseModel
from pydantic import ConfigDict, Field, field_validator


class BaseModel(_BaseModel):
    """
    Shared base for every schema in this file. Silences pydantic's
    "model_used conflicts with protected namespace 'model_'" warning
    (model_used is our own field name, not pydantic internals) without
    having to set model_config on each class individually -- every class
    below already does `class Foo(BaseModel):` so this propagates for free.
    """
    model_config = ConfigDict(protected_namespaces=())


def _truncate(text: str, max_len: int, suffix: str = "...") -> str:
    """
    Truncates `text` to at most `max_len` characters WITHOUT cutting a word
    in half -- backs up to the last whitespace before the limit and appends
    `suffix`, rather than a raw `text[:max_len]` slice that can chop off
    mid-word (e.g. "...driving customer c"). Falls back to a hard cut only
    if there's no whitespace within budget (one very long token).
    """
    text = text.strip()
    if len(text) <= max_len:
        return text
    budget = max_len - len(suffix)
    if budget <= 0:
        return text[:max_len]
    truncated = text[:budget]
    last_space = truncated.rfind(" ")
    if last_space > 0:
        truncated = truncated[:last_space]
    return truncated.rstrip(",.;:- ") + suffix





# ---------------------------------------------------------------------------
# Controlled vocabularies
# ---------------------------------------------------------------------------
# Keep these tight and business-relevant. The LLM is instructed to pick only
# from these values, and we validate on the way back in. If you find the
# model consistently wanting a category that isn't here, that's a signal to
# deliberately expand the list -- not a reason to let free text through.

class Sentiment(str, Enum):
    POSITIVE = "Positive"
    NEGATIVE = "Negative"
    NEUTRAL = "Neutral"
    MIXED = "Mixed"


class Severity(str, Enum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"
    CRITICAL = "Critical"


class Category(str, Enum):
    PRODUCT_QUALITY = "Product Quality"
    SERVICE_SPEED = "Service Speed"
    STAFF_BEHAVIOR = "Staff Behavior"
    CLEANLINESS = "Cleanliness"
    PRICING_BILLING = "Pricing/Billing"
    WAIT_TIME = "Wait Time"
    TECHNICAL_POS = "Technical/POS"
    ORDER_ACCURACY = "Order Accuracy"
    FACILITY_MAINTENANCE = "Facility/Maintenance"
    POLICY_PROCESS = "Policy/Process"
    OTHER = "Other"


class Department(str, Enum):
    OPERATIONS = "Operations"
    CUSTOMER_SERVICE = "Customer Service"
    PRODUCT = "Product"
    HR_TRAINING = "HR/Training"
    IT = "IT"
    FINANCE = "Finance"
    MANAGEMENT = "Management"
    OTHER = "Other"


# ---------------------------------------------------------------------------
# Input: one row of Phase 1's cleaned output
# ---------------------------------------------------------------------------

class ComplaintInput(BaseModel):
    """
    What Agent 1 expects to receive for a single complaint, coming out of
    Phase 1 (data/cleaned/*.csv).

    Only complaint_id and customer_text are required. Everything else is
    optional context that gets passed through untouched and re-attached
    to the output row -- it is NOT sent to the LLM as ground truth, just
    used for storage/joins later.
    """

    complaint_id: str
    source: Optional[str] = None
    store: Optional[str] = None
    date: Optional[str] = None
    customer_text: str = Field(..., min_length=1)

    @field_validator("customer_text")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("customer_text cannot be blank")
        return v.strip()


# ---------------------------------------------------------------------------
# Output: what the LLM must return, validated on the way back in
# ---------------------------------------------------------------------------

class ClassificationResult(BaseModel):
    """Structured output of Agent 1 for a single complaint."""

    complaint_id: str
    category: Category
    sentiment: Sentiment
    severity: Severity
    department: Department
    confidence: float = Field(..., ge=0.0, le=1.0)
    reasoning: Optional[str] = Field(
        default=None,
        description="One-sentence justification the LLM gave for its call. "
        "Kept short; useful for QA/debugging, not shown to end users.",
    )

    # Metadata about how this result was produced (not part of the LLM's
    # JSON -- filled in by the agent after the call).
    model_used: Optional[str] = None
    classified_at: Optional[str] = None

    @field_validator("reasoning")
    @classmethod
    def trim_reasoning(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        return _truncate(v, 300)


class ClassifiedComplaint(BaseModel):
    """
    Full row that gets written back out: original input fields + the
    classification. This is the shape that flows into Phase 4 storage.
    Matches the master schema minus root_cause/recommendation (Phase 3).
    """

    complaint_id: str
    source: Optional[str] = None
    store: Optional[str] = None
    date: Optional[str] = None
    customer_text: str
    sentiment: Sentiment
    category: Category
    department: Department
    severity: Severity
    confidence: float
    model_used: Optional[str] = None
    classified_at: Optional[str] = None

    @classmethod
    def from_input_and_result(
        cls, complaint: ComplaintInput, result: ClassificationResult
    ) -> "ClassifiedComplaint":
        return cls(
            complaint_id=complaint.complaint_id,
            source=complaint.source,
            store=complaint.store,
            date=complaint.date,
            customer_text=complaint.customer_text,
            sentiment=result.sentiment,
            category=result.category,
            department=result.department,
            severity=result.severity,
            confidence=result.confidence,
            model_used=result.model_used,
            classified_at=result.classified_at or datetime.utcnow().isoformat(),
        )


# ===========================================================================
# PHASE 3 — Multi-agent workflow schemas
#
# Design principle carried through every stage below: the LLM is only ever
# asked to produce the *judgment* part of a payload (a diagnosis, a
# narrative, a recommendation). Anything countable or joinable -- IDs,
# frequencies, which complaints belong to a pattern -- is assigned by
# deterministic Python code in the agent, never by the model. This keeps
# IDs stable/foreign-key-safe and stops the LLM from silently inventing or
# dropping a complaint_id. Each stage therefore has two models:
#   *LLMOutput  -> exactly what we ask the model to return as JSON
#   <FinalName> -> LLMOutput + IDs/metadata the agent stitches on afterward
# ===========================================================================

# ---------------------------------------------------------------------------
# Agent 2 — Root Cause Analysis (runs per complaint)
# ---------------------------------------------------------------------------

class RootCauseCategory(str, Enum):
    STAFFING_SHORTAGE = "Staffing Shortage"
    TRAINING_GAP = "Training Gap"
    PROCESS_FAILURE = "Process Failure"
    EQUIPMENT_TECHNICAL_FAILURE = "Equipment/Technical Failure"
    POLICY_GAP = "Policy Gap"
    SUPPLY_VENDOR_ISSUE = "Supply/Vendor Issue"
    COMMUNICATION_BREAKDOWN = "Communication Breakdown"
    MANAGEMENT_OVERSIGHT = "Management Oversight"
    SYSTEMIC_DESIGN_FLAW = "Systemic/Design Flaw"
    OTHER = "Other"


class RootCauseLLMOutput(BaseModel):
    """Exactly what Agent 2 asks the model to return for one complaint."""

    root_cause_category: RootCauseCategory
    root_cause_explanation: str = Field(
        ..., min_length=1,
        description="1-3 sentences on WHY this happened operationally -- "
        "not a restatement of what the customer said.",
    )
    contributing_factors: list[str] = Field(
        default_factory=list,
        description="Short phrases, e.g. ['understaffed during peak hours', 'no POS backup procedure']",
    )
    confidence: float = Field(..., ge=0.0, le=1.0)

    @field_validator("root_cause_explanation")
    @classmethod
    def trim_explanation(cls, v: str) -> str:
        return _truncate(v, 500)

    @field_validator("contributing_factors")
    @classmethod
    def cap_factors(cls, v: list[str]) -> list[str]:
        return [f.strip() for f in v[:5] if f and f.strip()]


class RootCauseResult(RootCauseLLMOutput):
    """LLM output + the identifying/metadata fields the agent attaches."""

    complaint_id: str
    model_used: Optional[str] = None
    analyzed_at: Optional[str] = None

    @classmethod
    def from_llm_output(
        cls, complaint_id: str, llm_output: RootCauseLLMOutput,
        model_used: str, analyzed_at: Optional[str] = None,
    ) -> "RootCauseResult":
        return cls(
            complaint_id=complaint_id,
            model_used=model_used,
            analyzed_at=analyzed_at or datetime.utcnow().isoformat(),
            **llm_output.model_dump(),
        )


class RootCausedComplaint(ClassifiedComplaint):
    """Full row flowing into Agent 3: classification + root cause."""

    root_cause_category: RootCauseCategory
    root_cause_explanation: str
    contributing_factors: list[str] = Field(default_factory=list)
    root_cause_confidence: float

    @classmethod
    def from_classified_and_result(
        cls, complaint: ClassifiedComplaint, result: RootCauseResult
    ) -> "RootCausedComplaint":
        return cls(
            **complaint.model_dump(),
            root_cause_category=result.root_cause_category,
            root_cause_explanation=result.root_cause_explanation,
            contributing_factors=result.contributing_factors,
            root_cause_confidence=result.confidence,
        )


# ---------------------------------------------------------------------------
# Agent 3 — Pattern Detection (runs once over the whole batch)
# ---------------------------------------------------------------------------

class TrendDirection(str, Enum):
    INCREASING = "Increasing"
    STABLE = "Stable"
    DECREASING = "Decreasing"
    INSUFFICIENT_DATA = "Insufficient Data"


class PatternNarrativeLLMOutput(BaseModel):
    """
    The LLM is only asked to name/describe a pre-clustered group of
    complaints and judge its trend -- the cluster membership, counts, and
    severity breakdown are computed deterministically before this is called.
    """

    title: str = Field(..., min_length=1)
    description: str = Field(
        ..., min_length=1,
        description="2-4 sentences: what the recurring issue is and why it "
        "qualifies as a pattern rather than an isolated incident.",
    )
    trend: TrendDirection

    @field_validator("title")
    @classmethod
    def trim_title(cls, v: str) -> str:
        return _truncate(v, 100)

    @field_validator("description")
    @classmethod
    def trim_description(cls, v: str) -> str:
        return _truncate(v, 600)


class Pattern(BaseModel):
    """A detected recurring issue: deterministic cluster stats + LLM narrative."""

    pattern_id: str
    title: str
    description: str
    trend: TrendDirection
    root_cause_category: RootCauseCategory
    department: Department
    category: Category
    stores_affected: list[str] = Field(default_factory=list)
    complaint_ids: list[str] = Field(default_factory=list)
    frequency: int = Field(..., ge=1)
    severity_breakdown: dict[str, int] = Field(default_factory=dict)
    avg_confidence: float
    model_used: Optional[str] = None
    detected_at: Optional[str] = None


# ---------------------------------------------------------------------------
# Agent 4 — Recommendation Engine (runs per pattern)
# ---------------------------------------------------------------------------

class RecommendationType(str, Enum):
    OPERATIONAL = "Operational"
    PRODUCT = "Product"
    TRAINING = "Training"
    POLICY = "Policy"
    TECHNOLOGY = "Technology"


class ImpactLevel(str, Enum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"


class RecommendationLLMOutput(BaseModel):
    recommendation_type: RecommendationType
    title: str = Field(..., min_length=1)
    description: str = Field(
        ..., min_length=1,
        description="Concrete, actionable fix -- not a restatement of the problem.",
    )
    expected_impact: ImpactLevel
    effort_estimate: ImpactLevel
    owning_department: Department
    confidence: float = Field(..., ge=0.0, le=1.0)

    @field_validator("title")
    @classmethod
    def trim_title(cls, v: str) -> str:
        return _truncate(v, 120)

    @field_validator("description")
    @classmethod
    def trim_description(cls, v: str) -> str:
        return _truncate(v, 800)


class Recommendation(BaseModel):
    recommendation_id: str
    pattern_id: str
    recommendation_type: RecommendationType
    title: str
    description: str
    expected_impact: ImpactLevel
    effort_estimate: ImpactLevel
    owning_department: Department
    supporting_evidence: list[str] = Field(
        default_factory=list,
        description="Sample complaint_ids from the source pattern, capped for readability.",
    )
    confidence: float
    model_used: Optional[str] = None
    generated_at: Optional[str] = None

    @classmethod
    def from_pattern_and_result(
        cls, pattern: Pattern, recommendation_id: str,
        llm_output: RecommendationLLMOutput, model_used: str,
        evidence_cap: int = 5,
    ) -> "Recommendation":
        return cls(
            recommendation_id=recommendation_id,
            pattern_id=pattern.pattern_id,
            supporting_evidence=pattern.complaint_ids[:evidence_cap],
            model_used=model_used,
            generated_at=datetime.utcnow().isoformat(),
            **llm_output.model_dump(),
        )


# ---------------------------------------------------------------------------
# Agent 5 — PM / Jira Story Generator (runs per recommendation)
# ---------------------------------------------------------------------------

class StoryPriority(str, Enum):
    P0_CRITICAL = "P0 - Critical"
    P1_HIGH = "P1 - High"
    P2_MEDIUM = "P2 - Medium"
    P3_LOW = "P3 - Low"


class JiraStoryLLMOutput(BaseModel):
    title: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    acceptance_criteria: list[str] = Field(..., min_length=1)
    priority: StoryPriority
    labels: list[str] = Field(default_factory=list)
    story_points_estimate: Optional[int] = Field(default=None, ge=1, le=13)

    @field_validator("title")
    @classmethod
    def trim_title(cls, v: str) -> str:
        return _truncate(v, 150)

    @field_validator("acceptance_criteria")
    @classmethod
    def cap_criteria(cls, v: list[str]) -> list[str]:
        cleaned = [c.strip() for c in v if c and c.strip()]
        if not cleaned:
            raise ValueError("acceptance_criteria cannot be empty")
        return cleaned[:8]

    @field_validator("labels")
    @classmethod
    def cap_labels(cls, v: list[str]) -> list[str]:
        return [l.strip().lower().replace(" ", "-") for l in v[:6] if l and l.strip()]


class JiraStory(BaseModel):
    story_id: str
    recommendation_id: str
    title: str
    description: str
    acceptance_criteria: list[str]
    priority: StoryPriority
    labels: list[str] = Field(default_factory=list)
    story_points_estimate: Optional[int] = None
    model_used: Optional[str] = None
    generated_at: Optional[str] = None

    @classmethod
    def from_recommendation_and_result(
        cls, recommendation: Recommendation, story_id: str,
        llm_output: JiraStoryLLMOutput, model_used: str,
    ) -> "JiraStory":
        return cls(
            story_id=story_id,
            recommendation_id=recommendation.recommendation_id,
            model_used=model_used,
            generated_at=datetime.utcnow().isoformat(),
            **llm_output.model_dump(),
        )


# ---------------------------------------------------------------------------
# Agent 6 — Executive Summary (runs once at the end)
# ---------------------------------------------------------------------------

class ExecutiveSummaryLLMOutput(BaseModel):
    headline: str = Field(..., min_length=1)
    key_findings: list[str] = Field(..., min_length=1)
    top_risk_areas: list[str] = Field(default_factory=list)
    narrative: str = Field(..., min_length=1)

    @field_validator("headline")
    @classmethod
    def trim_headline(cls, v: str) -> str:
        return _truncate(v, 150)

    @field_validator("key_findings")
    @classmethod
    def cap_findings(cls, v: list[str]) -> list[str]:
        cleaned = [f.strip() for f in v if f and f.strip()]
        if not cleaned:
            raise ValueError("key_findings cannot be empty")
        return cleaned[:10]

    @field_validator("top_risk_areas")
    @classmethod
    def cap_risks(cls, v: list[str]) -> list[str]:
        return [r.strip() for r in v[:5] if r and r.strip()]


class ExecutiveSummary(BaseModel):
    summary_id: str
    generated_at: str
    period_covered: Optional[str] = None
    total_complaints_analyzed: int
    total_patterns_detected: int
    total_recommendations: int
    headline: str
    key_findings: list[str]
    top_risk_areas: list[str] = Field(default_factory=list)
    narrative: str
    model_used: Optional[str] = None

    @classmethod
    def from_llm_output(
        cls, summary_id: str, llm_output: ExecutiveSummaryLLMOutput,
        model_used: str, total_complaints_analyzed: int,
        total_patterns_detected: int, total_recommendations: int,
        period_covered: Optional[str] = None,
    ) -> "ExecutiveSummary":
        return cls(
            summary_id=summary_id,
            generated_at=datetime.utcnow().isoformat(),
            period_covered=period_covered,
            total_complaints_analyzed=total_complaints_analyzed,
            total_patterns_detected=total_patterns_detected,
            total_recommendations=total_recommendations,
            model_used=model_used,
            **llm_output.model_dump(),
        )
