"""
data/load_outputs.py

Bridges your ALREADY-BUILT Phase 1/2/3 pipeline to Phase 4 storage.

This does NOT re-run any agent or call any LLM. It reads the files your
pipeline already produces on disk and loads them into the database via
data/repository.py. Safe to run repeatedly (every save_* in repository.py
upserts via db.merge).

FILE MAP (matches your actual pipeline output, not the spec in isolation):

    Phase 1 -- data/ingestion/pipeline.py
        data/cleaned/master_cleaned.csv   -> StandardComplaint rows (base Complaint only)

    Phase 2 -- agents/classification_agent.py
        outputs/classified_complaints.csv -> ClassifiedComplaint rows
                                              (Complaint upsert + ComplaintClassification)

    Phase 3 -- workflows/pipeline.py (PipelinePaths)
        outputs/root_caused_complaints.csv -> RootCausedComplaint rows -> RootCause
        outputs/patterns.json              -> list[Pattern]            -> Pattern (+ links)
        outputs/recommendations.json       -> list[Recommendation]     -> Recommendation
        outputs/jira_stories.json          -> list[JiraStory]          -> JiraStory
        outputs/executive_summary.json     -> ExecutiveSummary         -> ExecutiveSummary

NOTE: this file intentionally does NOT `import workflows.pipeline` -- that
module also imports your 5 agent files (root_cause_agent.py,
pattern_detection_agent.py, etc.), which pull in agents/config.py and
agents/openrouter_client.py and therefore require OPENROUTER_API_KEY to be
importable. Phase 4 has no business needing an API key just to load
already-generated output files, so the checkpoint filenames below are kept
in sync with workflows/pipeline.py's PipelinePaths by convention/comment,
not by import.

Run:
    python -m data.load_outputs
    python -m data.load_outputs --cleaned-dir data/cleaned --outputs-dir outputs
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Optional

import pandas as pd

from agents.schemas import (
    ClassifiedComplaint,
    ExecutiveSummary as ExecutiveSummarySchema,
    JiraStory as JiraStorySchema,
    Pattern as PatternSchema,
    Recommendation as RecommendationSchema,
    RootCausedComplaint,
)
from data.database import PROJECT_ROOT, session_scope
from data.repository import (
    save_classification,
    save_complaint,
    save_executive_summary,
    save_jira_story,
    save_pattern,
    save_recommendation,
    save_root_cause,
)
from agents.schemas import ComplaintInput


# ---------------------------------------------------------------------------
# Phase 1: data/cleaned/master_cleaned.csv (data/ingestion/schema.py's
# StandardComplaint, STANDARD_COLUMNS order). Only the base fields
# (complaint_id/source/store/date/customer_text) are loaded here -- the
# AI-derived columns on this file are blank placeholders per schema.py's
# own docstring and get filled in properly via the Phase 2/3 loaders below.
# ---------------------------------------------------------------------------

def load_master_cleaned(csv_path: Path) -> int:
    if not csv_path.exists():
        print(f"⚠️  {csv_path} not found -- skipping Phase 1 load")
        return 0

    df = pd.read_csv(csv_path, dtype=str)
    df = df.where(pd.notnull(df), None)

    count = 0
    with session_scope() as db:
        for row in df.to_dict(orient="records"):
            complaint = ComplaintInput(
                complaint_id=row["complaint_id"],
                source=row.get("source"),
                store=row.get("store"),
                date=row.get("date"),
                customer_text=row["customer_text"],
            )
            save_complaint(db, complaint, source_table="master_cleaned")
            count += 1
    print(f"✅ Phase 1: {count} complaints loaded from {csv_path}")
    return count


# ---------------------------------------------------------------------------
# Phase 2: outputs/classified_complaints.csv (agents/schemas.py
# ClassifiedComplaint). Upserts BOTH the base Complaint row (in case Phase
# 1's master_cleaned.csv wasn't loaded first) and the classification.
# ---------------------------------------------------------------------------

def load_classified_complaints(csv_path: Path) -> int:
    if not csv_path.exists():
        print(f"⚠️  {csv_path} not found -- skipping Phase 2 load")
        return 0

    df = pd.read_csv(csv_path)
    df = df.where(pd.notnull(df), None)

    count = 0
    with session_scope() as db:
        for row in df.to_dict(orient="records"):
            classified = ClassifiedComplaint(**row)
            save_complaint(
                db,
                ComplaintInput(
                    complaint_id=classified.complaint_id,
                    source=classified.source,
                    store=classified.store,
                    date=classified.date,
                    customer_text=classified.customer_text,
                ),
                source_table="classified_complaints",
            )
            save_classification(db, classified)
            count += 1
    print(f"✅ Phase 2: {count} classifications loaded from {csv_path}")
    return count


# ---------------------------------------------------------------------------
# Phase 3, stage 1: outputs/root_caused_complaints.csv. Uses the identical
# contributing_factors string->list parsing as workflows/pipeline.py's own
# _load_root_caused() so this stays byte-for-byte consistent with how the
# pipeline reads its own checkpoint back.
# ---------------------------------------------------------------------------

def load_root_caused(csv_path: Path) -> int:
    if not csv_path.exists():
        print(f"⚠️  {csv_path} not found -- skipping root cause load")
        return 0

    df = pd.read_csv(csv_path)
    df = df.where(pd.notnull(df), None)
    records = df.to_dict(orient="records")
    for r in records:
        if isinstance(r.get("contributing_factors"), str):
            try:
                r["contributing_factors"] = json.loads(r["contributing_factors"].replace("'", '"'))
            except (json.JSONDecodeError, AttributeError):
                r["contributing_factors"] = []

    count = 0
    with session_scope() as db:
        for r in records:
            root_caused = RootCausedComplaint(**r)
            save_root_cause(db, root_caused)
            count += 1
    print(f"✅ Phase 3 (root cause): {count} rows loaded from {csv_path}")
    return count


# ---------------------------------------------------------------------------
# Phase 3, stages 2-5: JSON checkpoints, each a straightforward
# list[Model] or single Model -- same shape workflows/pipeline.py itself
# reads back in when skipping an already-completed stage.
# ---------------------------------------------------------------------------

def load_patterns(json_path: Path) -> int:
    if not json_path.exists():
        print(f"⚠️  {json_path} not found -- skipping patterns load")
        return 0
    patterns = [PatternSchema(**row) for row in json.loads(json_path.read_text())]
    with session_scope() as db:
        for p in patterns:
            save_pattern(db, p)
    print(f"✅ Phase 3 (patterns): {len(patterns)} loaded from {json_path}")
    return len(patterns)


def load_recommendations(json_path: Path) -> int:
    if not json_path.exists():
        print(f"⚠️  {json_path} not found -- skipping recommendations load")
        return 0
    recs = [RecommendationSchema(**row) for row in json.loads(json_path.read_text())]
    with session_scope() as db:
        for r in recs:
            save_recommendation(db, r)
    print(f"✅ Phase 3 (recommendations): {len(recs)} loaded from {json_path}")
    return len(recs)


def load_jira_stories(json_path: Path) -> int:
    if not json_path.exists():
        print(f"⚠️  {json_path} not found -- skipping Jira stories load")
        return 0
    stories = [JiraStorySchema(**row) for row in json.loads(json_path.read_text())]
    with session_scope() as db:
        for s in stories:
            save_jira_story(db, s)
    print(f"✅ Phase 3 (Jira stories): {len(stories)} loaded from {json_path}")
    return len(stories)


def load_executive_summary(json_path: Path) -> int:
    if not json_path.exists():
        print(f"⚠️  {json_path} not found -- skipping executive summary load")
        return 0
    summary = ExecutiveSummarySchema(**json.loads(json_path.read_text()))
    with session_scope() as db:
        save_executive_summary(db, summary)
    print(f"✅ Phase 3 (executive summary): loaded from {json_path}")
    return 1


# ---------------------------------------------------------------------------
# Orchestrator -- mirrors workflows/pipeline.py's STAGE_ORDER, skipping any
# file that doesn't exist yet rather than failing (same "not every phase
# has necessarily produced every file yet" tolerance the pipeline itself
# uses for checkpoints).
# ---------------------------------------------------------------------------

def load_all(cleaned_dir: Optional[Path] = None, outputs_dir: Optional[Path] = None) -> None:
    cleaned_dir = cleaned_dir or (PROJECT_ROOT / "data" / "cleaned")
    outputs_dir = outputs_dir or (PROJECT_ROOT / "outputs")

    print(f"Loading from cleaned_dir={cleaned_dir}, outputs_dir={outputs_dir}\n")

    # Phase 1 (optional -- Phase 2's CSV already carries base fields too,
    # so this is only needed if you want unclassified complaints in the DB)
    load_master_cleaned(cleaned_dir / "master_cleaned.csv")

    # Phase 2
    load_classified_complaints(outputs_dir / "classified_complaints.csv")

    # Phase 3 (in pipeline dependency order -- patterns before
    # recommendations before stories, since each FKs to the previous)
    load_root_caused(outputs_dir / "root_caused_complaints.csv")
    load_patterns(outputs_dir / "patterns.json")
    load_recommendations(outputs_dir / "recommendations.json")
    load_jira_stories(outputs_dir / "jira_stories.json")
    load_executive_summary(outputs_dir / "executive_summary.json")


def main() -> None:
    parser = argparse.ArgumentParser(description="Load Phase 1/2/3 pipeline outputs into the Phase 4 database")
    parser.add_argument("--cleaned-dir", type=Path, default=None, help="Defaults to data/cleaned")
    parser.add_argument("--outputs-dir", type=Path, default=None, help="Defaults to outputs")
    args = parser.parse_args()
    load_all(cleaned_dir=args.cleaned_dir, outputs_dir=args.outputs_dir)


if __name__ == "__main__":
    main()
