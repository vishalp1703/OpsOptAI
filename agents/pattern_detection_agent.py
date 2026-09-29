"""
agents/pattern_detection_agent.py

Agent 3 -- Pattern Detection.

Runs once over the full batch of root-caused complaints. Clusters
deterministically (pandas groupby on category + department + root cause
category), then for each cluster that meets MIN_CLUSTER_SIZE, asks the LLM
to write a human-readable title/description and judge the trend direction.

Why deterministic clustering + LLM narrative (not "dump everything into one
giant prompt"):
  - Counts, store lists, and severity breakdowns must be exactly right --
    an LLM asked to count across 200 rows in one prompt will approximate,
    not count. Pandas doesn't.
  - Keeping cluster membership deterministic also means Pattern.complaint_ids
    is always a real, reproducible list of complaint_ids, which Phase 4
    storage and Phase 5 dashboard drill-downs depend on.

CLI:
    python -m agents.pattern_detection_agent \\
        --input outputs/root_caused_complaints.csv \\
        --output outputs/patterns.json
"""

from __future__ import annotations

import argparse
import json
import logging
import uuid
from pathlib import Path

import pandas as pd

from agents.config import ModelSpec
from agents.llm_json import LLMParseError, call_and_parse
from agents.model_selector import resolve_model
from agents.openrouter_client import OpenRouterError, OpenRouterTransientError
from agents.schemas import Pattern, PatternNarrativeLLMOutput, RootCausedComplaint
from prompts.pattern_detection_prompt import SYSTEM_PROMPT, build_user_prompt

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

# A cluster smaller than this is treated as an isolated incident, not a
# pattern worth surfacing to leadership -- tune based on data volume.
MIN_CLUSTER_SIZE = 3
SAMPLE_EXCERPTS_PER_CLUSTER = 5
TIME_BUCKETS = 3  # chronological thirds, used for the trend judgment


class PatternDetectionAgent:
    def __init__(self, model_key: str | None = None, min_cluster_size: int = MIN_CLUSTER_SIZE):
        self.model_spec: ModelSpec = resolve_model(model_key)
        self.min_cluster_size = min_cluster_size
        logger.info("PatternDetectionAgent using model: %s (%s)", self.model_spec.label, self.model_spec.slug)

    def detect(self, complaints: list[RootCausedComplaint]) -> list[Pattern]:
        if not complaints:
            logger.warning("No complaints supplied to pattern detection.")
            return []

        df = pd.DataFrame([c.model_dump() for c in complaints])
        # Keep the raw ClassifiedComplaint objects addressable by id for
        # excerpt sampling, since the DataFrame loses type info.
        by_id = {c.complaint_id: c for c in complaints}

        patterns: list[Pattern] = []
        group_cols = ["category", "department", "root_cause_category"]
        for group_key, group_df in df.groupby(group_cols):
            if len(group_df) < self.min_cluster_size:
                continue

            category, department, root_cause_category = group_key
            complaint_ids = group_df["complaint_id"].tolist()
            stores_affected = sorted({s for s in group_df["store"].dropna().tolist() if s})
            severity_breakdown = {
                self._enum_str(k): int(v)
                for k, v in group_df["severity"].value_counts().to_dict().items()
            }
            avg_confidence = float(group_df["root_cause_confidence"].mean())
            time_buckets = self._time_bucket_counts(group_df)
            sample_excerpts = [
                by_id[cid].customer_text[:200]
                for cid in complaint_ids[:SAMPLE_EXCERPTS_PER_CLUSTER]
            ]

            try:
                narrative = self._synthesize_narrative(
                    category=self._enum_str(category),
                    department=self._enum_str(department),
                    root_cause_category=self._enum_str(root_cause_category),
                    frequency=len(group_df),
                    stores_affected=stores_affected,
                    severity_breakdown=severity_breakdown,
                    time_buckets=time_buckets,
                    sample_excerpts=sample_excerpts,
                )
            except (LLMParseError, OpenRouterError, OpenRouterTransientError) as exc:
                logger.error(
                    "Pattern narrative synthesis failed for cluster (%s, %s, %s): %s",
                    category, department, root_cause_category, exc,
                )
                continue

            llm_output, served_by = narrative
            patterns.append(
                Pattern(
                    pattern_id=f"PAT-{uuid.uuid4().hex[:8]}",
                    title=llm_output.title,
                    description=llm_output.description,
                    trend=llm_output.trend,
                    root_cause_category=root_cause_category,
                    department=department,
                    category=category,
                    stores_affected=stores_affected,
                    complaint_ids=complaint_ids,
                    frequency=len(group_df),
                    severity_breakdown=severity_breakdown,
                    avg_confidence=round(avg_confidence, 3),
                    model_used=served_by,
                    detected_at=pd.Timestamp.now("UTC").isoformat(),
                )
            )

        # Highest-frequency, lowest-confidence-diagnosis patterns first --
        # these are the ones leadership should see at the top.
        patterns.sort(key=lambda p: p.frequency, reverse=True)
        logger.info(
            "Pattern detection complete: %d pattern(s) found from %d complaints (min cluster size=%d).",
            len(patterns), len(complaints), self.min_cluster_size,
        )
        return patterns

    def _synthesize_narrative(
        self, category, department, root_cause_category, frequency,
        stores_affected, severity_breakdown, time_buckets, sample_excerpts,
    ) -> tuple[PatternNarrativeLLMOutput, str]:
        user_prompt = build_user_prompt(
            category=category,
            department=department,
            root_cause_category=root_cause_category,
            frequency=frequency,
            stores_affected=stores_affected,
            severity_breakdown=severity_breakdown,
            time_buckets=time_buckets,
            sample_excerpts=sample_excerpts,
        )
        return call_and_parse(
            model_slug=self.model_spec.slug,
            system_prompt=SYSTEM_PROMPT,
            user_prompt=user_prompt,
            result_model=PatternNarrativeLLMOutput,
        )

    @staticmethod
    def _time_bucket_counts(group_df: pd.DataFrame) -> list[int]:
        """
        Splits the cluster into TIME_BUCKETS chronological, roughly-equal
        chunks by parsed date and returns the count per chunk -- cheap,
        deterministic evidence for the LLM's trend judgment. Returns an
        empty list if dates can't be parsed (LLM will then correctly say
        "Insufficient Data").
        """
        dates = pd.to_datetime(group_df["date"], errors="coerce").dropna()
        if len(dates) < 2:
            return []
        sorted_dates = dates.sort_values()
        try:
            chunks = pd.qcut(range(len(sorted_dates)), q=min(TIME_BUCKETS, len(sorted_dates)), duplicates="drop")
        except ValueError:
            return []
        return sorted_dates.groupby(chunks, observed=True).size().tolist()

    @staticmethod
    def _enum_str(value) -> str:
        """
        Normalizes a value that may be a str-subclassed Enum member (e.g.
        Severity.HIGH) or a plain string into its clean display string
        ("High"), never Python's `str(EnumMember)` repr ("Severity.HIGH").
        """
        return value.value if hasattr(value, "value") else str(value)


def _load_root_caused_complaints(csv_path: Path) -> list[RootCausedComplaint]:
    df = pd.read_csv(csv_path)
    df = df.where(pd.notnull(df), None)
    records = df.to_dict(orient="records")
    for r in records:
        if isinstance(r.get("contributing_factors"), str):
            try:
                r["contributing_factors"] = json.loads(r["contributing_factors"].replace("'", '"'))
            except (json.JSONDecodeError, AttributeError):
                r["contributing_factors"] = []
    return [RootCausedComplaint(**r) for r in records]


def main() -> None:
    parser = argparse.ArgumentParser(description="Agent 3 -- Pattern Detection")
    parser.add_argument("--input", type=Path, default=Path("outputs/root_caused_complaints.csv"))
    parser.add_argument("--output", type=Path, default=Path("outputs/patterns.json"))
    parser.add_argument("--model", type=str, default=None)
    parser.add_argument("--min-cluster-size", type=int, default=MIN_CLUSTER_SIZE)
    args = parser.parse_args()

    complaints = _load_root_caused_complaints(args.input)
    logger.info("Loaded %d root-caused complaints from %s", len(complaints), args.input)

    agent = PatternDetectionAgent(model_key=args.model, min_cluster_size=args.min_cluster_size)
    patterns = agent.detect(complaints)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps([p.model_dump() for p in patterns], indent=2, default=str))
    logger.info("Wrote %d patterns -> %s", len(patterns), args.output)


if __name__ == "__main__":
    main()
