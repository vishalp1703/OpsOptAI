"""
data/test_phase4.py

Standalone integration test for Phase 4 (not pytest -- zero-dependency
script so it can run before pytest is even in requirements.txt).

Simulates one complaint traveling through every agent stage using the
REAL Pydantic schemas from agents/schemas.py, persists each stage via
data/repository.py, then queries it all back and asserts the full chain
holds together (FKs, relationships, JSON round-tripping, enum storage).

Run:
    python -m data.test_phase4
"""

from __future__ import annotations

from datetime import datetime, timezone

from agents.schemas import (
    Category,
    ClassificationResult,
    ClassifiedComplaint,
    ComplaintInput,
    Department,
    ExecutiveSummary as ExecutiveSummarySchema,
    ExecutiveSummaryLLMOutput,
    ImpactLevel,
    JiraStory as JiraStorySchema,
    JiraStoryLLMOutput,
    Pattern as PatternSchema,
    PatternNarrativeLLMOutput,
    Recommendation as RecommendationSchema,
    RecommendationLLMOutput,
    RecommendationType,
    RootCauseCategory,
    RootCauseLLMOutput,
    RootCauseResult,
    RootCausedComplaint,
    Sentiment,
    Severity,
    StoryPriority,
    TrendDirection,
)
from data.database import init_db, session_scope
from data.repository import (
    get_complaint,
    save_classification,
    save_complaint,
    save_executive_summary,
    save_jira_story,
    save_pattern,
    save_recommendation,
    save_root_cause,
)
from data.models import Complaint, ComplaintClassification, JiraStory, Pattern, Recommendation, RootCause

NOW = datetime.now(timezone.utc).isoformat()


def run() -> None:
    print("Resetting schema for a clean test run...")
    init_db(drop_first=True)

    # --- Stage 0: two complaints coming out of Phase 1 -----------------
    complaint_a = ComplaintInput(
        complaint_id="CMP0001",
        source="customer_reviews",
        store="Store-101 Downtown",
        date="2026-07-15",
        customer_text="Waited 25 minutes at checkout because only one register was open.",
    )
    complaint_b = ComplaintInput(
        complaint_id="CMP0002",
        source="support_tickets",
        store="Store-101 Downtown",
        date="2026-07-16",
        customer_text="Curbside pickup order sat for an hour, nobody brought it out.",
    )

    with session_scope() as db:
        save_complaint(db, complaint_a, source_table="raw_customer_reviews", source_row_id=1)
        save_complaint(db, complaint_b, source_table="raw_support_tickets", source_row_id=1)
    print("✅ Stage 0: 2 complaints saved")

    # --- Stage 1: Agent 1 classification (Phase 2) ----------------------
    classification_a = ClassificationResult(
        complaint_id="CMP0001", category=Category.WAIT_TIME, sentiment=Sentiment.NEGATIVE,
        severity=Severity.MEDIUM, department=Department.OPERATIONS, confidence=0.91,
        reasoning="Long checkout wait due to understaffing.", model_used="gpt", classified_at=NOW,
    )
    classification_b = ClassificationResult(
        complaint_id="CMP0002", category=Category.SERVICE_SPEED, sentiment=Sentiment.NEGATIVE,
        severity=Severity.HIGH, department=Department.OPERATIONS, confidence=0.88,
        reasoning="Curbside SLA breach.", model_used="gpt", classified_at=NOW,
    )
    classified_a = ClassifiedComplaint.from_input_and_result(complaint_a, classification_a)
    classified_b = ClassifiedComplaint.from_input_and_result(complaint_b, classification_b)

    with session_scope() as db:
        save_classification(db, classified_a)
        save_classification(db, classified_b)
    print("✅ Stage 1: 2 classifications saved")

    # --- Stage 2: Agent 2 root cause (Phase 3) --------------------------
    rc_llm_a = RootCauseLLMOutput(
        root_cause_category=RootCauseCategory.STAFFING_SHORTAGE,
        root_cause_explanation="Only one register staffed during peak lunch hour.",
        contributing_factors=["understaffed during peak hours", "no overflow register policy"],
        confidence=0.87,
    )
    rc_result_a = RootCauseResult.from_llm_output("CMP0001", rc_llm_a, model_used="claude")
    root_caused_a = RootCausedComplaint.from_classified_and_result(classified_a, rc_result_a)

    rc_llm_b = RootCauseLLMOutput(
        root_cause_category=RootCauseCategory.PROCESS_FAILURE,
        root_cause_explanation="No notification workflow alerts staff when a curbside order is ready and waiting.",
        contributing_factors=["no curbside SLA monitoring", "manual order handoff"],
        confidence=0.83,
    )
    rc_result_b = RootCauseResult.from_llm_output("CMP0002", rc_llm_b, model_used="claude")
    root_caused_b = RootCausedComplaint.from_classified_and_result(classified_b, rc_result_b)

    with session_scope() as db:
        save_root_cause(db, root_caused_a)
        save_root_cause(db, root_caused_b)
    print("✅ Stage 2: 2 root causes saved")

    # --- Stage 3: Agent 3 pattern detection (Phase 3) -------------------
    pattern_narrative = PatternNarrativeLLMOutput(
        title="Checkout & fulfillment delays at Store-101",
        description="Multiple complaints point to Store-101 struggling with staffing coverage "
        "during peak hours, affecting both checkout and curbside fulfillment.",
        trend=TrendDirection.INCREASING,
    )
    pattern = PatternSchema(
        pattern_id="PAT0001",
        title=pattern_narrative.title,
        description=pattern_narrative.description,
        trend=pattern_narrative.trend,
        root_cause_category=RootCauseCategory.STAFFING_SHORTAGE,
        department=Department.OPERATIONS,
        category=Category.WAIT_TIME,
        stores_affected=["Store-101 Downtown"],
        complaint_ids=["CMP0001", "CMP0002"],
        frequency=2,
        severity_breakdown={"Medium": 1, "High": 1},
        avg_confidence=0.895,
        model_used="gemini",
        detected_at=NOW,
    )

    with session_scope() as db:
        save_pattern(db, pattern)
    print("✅ Stage 3: 1 pattern saved (linked to 2 complaints)")

    # --- Stage 4: Agent 4 recommendation (Phase 3) ----------------------
    rec_llm = RecommendationLLMOutput(
        recommendation_type=RecommendationType.OPERATIONAL,
        title="Add a peak-hour overflow staffing rule for Store-101",
        description="Introduce a trigger-based overflow policy: when checkout queue exceeds 3 "
        "customers or a curbside order waits >10 min, auto-page the nearest available associate.",
        expected_impact=ImpactLevel.HIGH,
        effort_estimate=ImpactLevel.MEDIUM,
        owning_department=Department.OPERATIONS,
        confidence=0.86,
    )
    recommendation = RecommendationSchema.from_pattern_and_result(
        pattern, recommendation_id="REC0001", llm_output=rec_llm, model_used="gemini",
    )

    with session_scope() as db:
        save_recommendation(db, recommendation)
    print("✅ Stage 4: 1 recommendation saved")

    # --- Stage 5: Agent 5 Jira story (Phase 3) --------------------------
    story_llm = JiraStoryLLMOutput(
        title="Implement peak-hour overflow staffing trigger for Store-101",
        description="As an Ops Manager, I want an automatic overflow-staffing trigger so that "
        "checkout and curbside delays during peak hours are resolved before they impact CSAT.",
        acceptance_criteria=[
            "Queue depth >3 triggers an overflow page within 60s",
            "Curbside orders waiting >10 min trigger an alert",
            "Dashboard shows overflow trigger frequency per store",
        ],
        priority=StoryPriority.P1_HIGH,
        labels=["operations", "staffing", "store-101"],
        story_points_estimate=5,
    )
    jira_story = JiraStorySchema.from_recommendation_and_result(
        recommendation, story_id="STORY0001", llm_output=story_llm, model_used="deepseek",
    )

    with session_scope() as db:
        save_jira_story(db, jira_story)
    print("✅ Stage 5: 1 Jira story saved")

    # --- Stage 6: Agent 6 executive summary (Phase 3) -------------------
    exec_llm = ExecutiveSummaryLLMOutput(
        headline="Store-101 peak-hour staffing gaps are driving checkout and curbside complaints",
        key_findings=[
            "2 of 2 sampled complaints trace back to Store-101 peak-hour understaffing",
            "Both checkout and curbside fulfillment are affected by the same root cause",
        ],
        top_risk_areas=["Store-101 Downtown operations"],
        narrative="Analysis of recent complaints at Store-101 reveals a single systemic root "
        "cause -- insufficient peak-hour staffing coverage -- manifesting across two different "
        "customer touchpoints. Addressing staffing coverage directly should resolve both.",
    )
    exec_summary = ExecutiveSummarySchema.from_llm_output(
        summary_id="SUM0001", llm_output=exec_llm, model_used="gpt",
        total_complaints_analyzed=2, total_patterns_detected=1, total_recommendations=1,
        period_covered="2026-07-15 to 2026-07-16",
    )

    with session_scope() as db:
        save_executive_summary(db, exec_summary)
    print("✅ Stage 6: 1 executive summary saved")

    # =====================================================================
    # VERIFICATION -- read everything back and assert the chain holds
    # =====================================================================
    print("\n--- Verifying persisted data ---")
    with session_scope() as db:
        c = get_complaint(db, "CMP0001")
        assert c is not None, "complaint not found"
        assert c.classification is not None, "classification relationship broken"
        assert c.classification.category == Category.WAIT_TIME
        assert c.root_cause is not None, "root_cause relationship broken"
        assert c.root_cause.root_cause_category == RootCauseCategory.STAFFING_SHORTAGE
        assert len(c.patterns) == 1, "complaint -> pattern relationship broken"
        assert c.patterns[0].pattern_id == "PAT0001"
        print(f"✅ Complaint {c.complaint_id}: classification, root cause, and pattern link all intact")

        p = db.get(Pattern, "PAT0001")
        assert len(p.complaints) == 2, "pattern -> complaints relationship broken"
        assert p.stores_affected == ["Store-101 Downtown"], "JSON list field didn't round-trip"
        assert p.severity_breakdown == {"Medium": 1, "High": 1}, "JSON dict field didn't round-trip"
        assert len(p.recommendations) == 1
        print(f"✅ Pattern {p.pattern_id}: linked to {len(p.complaints)} complaints, "
              f"stores_affected/severity_breakdown JSON round-tripped correctly")

        rec = p.recommendations[0]
        assert rec.jira_story is not None, "recommendation -> jira_story relationship broken"
        assert rec.jira_story.story_id == "STORY0001"
        assert rec.jira_story.acceptance_criteria == story_llm.acceptance_criteria
        print(f"✅ Recommendation {rec.recommendation_id}: linked to Jira story "
              f"{rec.jira_story.story_id}, acceptance_criteria JSON round-tripped correctly")

        summary = db.get(__import__("data.models", fromlist=["ExecutiveSummary"]).ExecutiveSummary, "SUM0001")
        assert summary.total_complaints_analyzed == 2
        print(f"✅ Executive summary {summary.summary_id}: {summary.headline!r}")

        # Cascade-delete check: deleting a Complaint should cascade to its
        # classification/root_cause rows but must NOT delete the Pattern
        # (it's still linked to CMP0002).
        db.delete(c)
        db.flush()
        assert db.get(ComplaintClassification, "CMP0001") is None, "cascade delete of classification failed"
        assert db.get(RootCause, "CMP0001") is None, "cascade delete of root_cause failed"
        assert db.get(Pattern, "PAT0001") is not None, "pattern was wrongly cascade-deleted"
        print("✅ Cascade delete: removing a Complaint correctly cascades to its "
              "classification/root_cause rows without touching the shared Pattern")
        db.rollback()  # don't actually persist the deletion -- this was just a cascade check

    print("\n🎉 ALL PHASE 4 CHECKS PASSED")


if __name__ == "__main__":
    run()
