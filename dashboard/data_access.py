"""
dashboard/data_access.py

Read-only data layer for the Streamlit dashboard.

WHY INTROSPECTION INSTEAD OF IMPORTING data/models.py DIRECTLY
----------------------------------------------------------------
This dashboard was written from your Phase 1-4 summary, not your actual
data/models.py source (it wasn't shared in this chat). Hard-importing your
ORM classes and referencing exact attribute names would risk an
AttributeError the moment column names don't match what I guessed.

Instead, this layer:
  1. Connects to the same DB Phase 4 already built (SQLite dev, or
     DATABASE_URL for prod — identical convention to data/database.py).
  2. Introspects each table's REAL columns at runtime via SQLAlchemy.
  3. Only ever selects columns that actually exist. Anything expected-but-
     missing is reported (not crashed on) in the "Data Health" panel in the
     sidebar, so you can see at a glance if a table/column name needs to be
     lined up with your real schema.

If something doesn't match, either rename the column on your end, or send
me your real data/models.py and I'll line these constants up exactly.
"""

import os
import sqlalchemy as sa
from sqlalchemy import inspect
import pandas as pd

# Same convention as Phase 4's data/database.py: SQLite by default, override
# with DATABASE_URL for Postgres in prod.
DEFAULT_SQLITE_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "opspilot.db")
DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{os.path.abspath(DEFAULT_SQLITE_PATH)}")

# Table -> columns we EXPECT based on your Phase 2-4 schemas.py / models.py summary.
# Used only for the Data Health report — never assumed to be present.
EXPECTED_SCHEMA = {
    "complaints": [
        "complaint_id", "source", "store", "date", "customer_text",
    ],
    "complaint_classification": [
        "complaint_id", "category", "sentiment", "severity", "department",
        "confidence", "model_used",
    ],
    "root_causes": [
        "complaint_id", "root_cause_category", "root_cause_explanation",
        "contributing_factors", "confidence", "model_used", "analyzed_at",
    ],
    "patterns": [
        "pattern_id", "title", "description", "trend", "stores_affected",
    ],
    "pattern_complaints": [
        "pattern_id", "complaint_id",
    ],
    "recommendations": [
        "recommendation_id", "pattern_id", "recommendation_type", "title",
        "description", "expected_impact", "effort_estimate",
        "owning_department", "supporting_evidence", "confidence",
        "model_used", "generated_at",
    ],
    "jira_stories": [
        "story_id", "recommendation_id", "title", "description",
        "acceptance_criteria", "priority", "labels", "story_points_estimate",
        "model_used", "generated_at",
    ],
    "executive_summaries": [
        "summary_id", "generated_at", "period_covered",
        "total_complaints_analyzed", "total_patterns_detected",
        "total_recommendations", "headline", "key_findings",
        "top_risk_areas", "narrative", "model_used",
    ],
}

# Fields the original Phase 5 spec asked for that do NOT exist anywhere in the
# Phase 1-4 schema (confirmed with you 2026-08-12). Surfaced explicitly in the
# UI as "not available yet" rather than faked.
KNOWN_GAPS = [
    ("Avg. resolution time", "No resolution/closed timestamp is captured anywhere in the "
                              "pipeline yet (only `date` = complaint received). Would need a "
                              "resolved_at field added at ingestion or via Phase 6 ticketing "
                              "integration."),
    ("Employee performance", "No employee identifier is captured as a structured field "
                              "(employee feedback is free text, not linked to a person/ID). "
                              "Would need an employee_id field on ingestion."),
]


def get_engine():
    return sa.create_engine(DATABASE_URL)


def introspect() -> dict:
    """
    Returns, per expected table:
      {"exists": bool, "columns": [real columns], "missing_expected": [expected but absent]}
    Never raises — a missing DB file just means everything reports as not-existing.
    """
    report = {}
    try:
        engine = get_engine()
        insp = inspect(engine)
        real_tables = set(insp.get_table_names())
    except Exception as e:
        for table, expected_cols in EXPECTED_SCHEMA.items():
            report[table] = {"exists": False, "columns": [], "missing_expected": expected_cols,
                              "error": str(e)}
        return report

    for table, expected_cols in EXPECTED_SCHEMA.items():
        if table not in real_tables:
            report[table] = {"exists": False, "columns": [], "missing_expected": expected_cols}
            continue
        real_cols = [c["name"] for c in insp.get_columns(table)]
        missing = [c for c in expected_cols if c not in real_cols]
        report[table] = {"exists": True, "columns": real_cols, "missing_expected": missing}
    return report


def safe_read_table(table: str, columns=None) -> pd.DataFrame:
    """
    Reads a table, silently dropping any requested columns that don't exist.
    Returns an empty DataFrame (never raises) if the table is missing.
    """
    try:
        engine = get_engine()
        insp = inspect(engine)
        if table not in insp.get_table_names():
            return pd.DataFrame()
        real_cols = [c["name"] for c in insp.get_columns(table)]
        cols_to_use = [c for c in columns if c in real_cols] if columns else real_cols
        if not cols_to_use:
            cols_to_use = real_cols
        col_sql = ", ".join(f'"{c}"' for c in cols_to_use)
        query = f'SELECT {col_sql} FROM "{table}"'
        with engine.connect() as conn:
            return pd.read_sql(sa.text(query), conn)
    except Exception:
        return pd.DataFrame()


def load_complaints_full() -> pd.DataFrame:
    """
    Builds one wide DataFrame approximating the StandardComplaint schema by
    left-joining complaints -> complaint_classification -> root_causes on
    complaint_id in pandas (avoids needing your exact ORM relationship names).
    Rows for complaints not yet classified/root-caused simply have NaNs in
    those columns — this is expected given only 12/320 rows are processed so far.
    """
    complaints = safe_read_table("complaints")
    classification = safe_read_table("complaint_classification")
    root_causes = safe_read_table("root_causes")

    if complaints.empty:
        return pd.DataFrame()

    df = complaints.copy()
    if "complaint_id" in df.columns:
        if not classification.empty and "complaint_id" in classification.columns:
            df = df.merge(classification, on="complaint_id", how="left", suffixes=("", "_cls"))
        if not root_causes.empty and "complaint_id" in root_causes.columns:
            # Real root_causes columns (confirmed against user's DB) collide on
            # confidence/model_used with complaint_classification, and use
            # root_cause_category/root_cause_explanation rather than a single
            # root_cause field — renamed here so the rest of the dashboard
            # (root cause distribution chart) keeps working unchanged.
            rc = root_causes.rename(columns={
                "root_cause_category": "root_cause",
                "root_cause_explanation": "root_cause_detail",
                "confidence": "root_cause_confidence",
                "model_used": "root_cause_model_used",
                "analyzed_at": "root_cause_analyzed_at",
            })
            df = df.merge(rc, on="complaint_id", how="left")

    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")

    if "confidence" in df.columns:
        df["confidence"] = pd.to_numeric(df["confidence"], errors="coerce")

    return df


def load_patterns_with_recommendations() -> pd.DataFrame:
    """
    Left-joins patterns -> recommendations -> jira_stories for the Patterns tab.

    recommendations and jira_stories both have their own title/description
    columns that collide with patterns' — prefixed with rec_/jira_ (rather
    than pandas' default _x/_y suffixing) so downstream code can reference
    them unambiguously.
    """
    patterns = safe_read_table("patterns")
    recs = safe_read_table("recommendations")
    jira = safe_read_table("jira_stories")

    if patterns.empty:
        return pd.DataFrame()

    df = patterns.copy()
    if "pattern_id" in df.columns and not recs.empty and "pattern_id" in recs.columns:
        recs_prefixed = recs.rename(columns={
            c: f"rec_{c}" for c in recs.columns if c not in ("pattern_id",)
        })
        df = df.merge(recs_prefixed, on="pattern_id", how="left")

        if "rec_recommendation_id" in df.columns and not jira.empty and "recommendation_id" in jira.columns:
            jira_prefixed = jira.rename(columns={
                c: f"jira_{c}" for c in jira.columns if c not in ("recommendation_id",)
            })
            df = df.merge(
                jira_prefixed,
                left_on="rec_recommendation_id",
                right_on="recommendation_id",
                how="left",
            )
    return df


def load_executive_summary() -> pd.DataFrame:
    return safe_read_table("executive_summaries")