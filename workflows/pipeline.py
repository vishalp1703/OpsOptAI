"""
workflows/pipeline.py

Phase 3 orchestrator. Chains:

    Agent 2 (Root Cause)      -- per complaint
        -> Agent 3 (Pattern Detection)  -- once over the batch
            -> Agent 4 (Recommendation) -- per pattern
                -> Agent 5 (PM/Jira Story) -- per recommendation
    Agent 6 (Executive Summary) -- once, over everything above

Each stage's output is checkpointed to outputs/ as it completes. This means
a failure in, say, Agent 4 doesn't force re-running (and re-paying for)
Agents 2 and 3 -- rerun the pipeline and stages whose checkpoint file
already exists are skipped by default (override with --force-stage / force
flags on run()).

CLI:
    python -m workflows.pipeline --input outputs/classified_complaints.csv

    # Force everything to rerun from scratch:
    python -m workflows.pipeline --input outputs/classified_complaints.csv --force

    # Force only pattern detection onward (e.g. after tuning MIN_CLUSTER_SIZE):
    python -m workflows.pipeline --input outputs/classified_complaints.csv --force-from patterns
"""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from agents.exec_summary_agent import ExecSummaryAgent
from agents.pattern_detection_agent import PatternDetectionAgent
from agents.pm_agent import PMAgent
from agents.recommendation_agent import RecommendationAgent
from agents.root_cause_agent import RootCauseAgent
from agents.schemas import (
    ClassifiedComplaint,
    ExecutiveSummary,
    JiraStory,
    Pattern,
    Recommendation,
    RootCausedComplaint,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

STAGE_ORDER = ["root_cause", "patterns", "recommendations", "stories", "summary"]


@dataclass
class PipelinePaths:
    output_dir: Path = Path("outputs")

    @property
    def root_caused(self) -> Path:
        return self.output_dir / "root_caused_complaints.csv"

    @property
    def root_cause_failed(self) -> Path:
        return self.output_dir / "root_cause_failed.json"

    @property
    def patterns(self) -> Path:
        return self.output_dir / "patterns.json"

    @property
    def recommendations(self) -> Path:
        return self.output_dir / "recommendations.json"

    @property
    def stories(self) -> Path:
        return self.output_dir / "jira_stories.json"

    @property
    def summary(self) -> Path:
        return self.output_dir / "executive_summary.json"

    @property
    def final_report(self) -> Path:
        return self.output_dir / "opspilot_report.json"


class Phase3Pipeline:
    """
    Orchestrates Agents 2-6. Each stage is independently resumable via its
    checkpoint file in outputs/ -- pass force=True (or force_from=<stage>)
    to rerun stages whose checkpoint already exists.
    """

    def __init__(self, model_key: str | None = None, paths: PipelinePaths | None = None):
        self.model_key = model_key
        self.paths = paths or PipelinePaths()
        self.paths.output_dir.mkdir(parents=True, exist_ok=True)

    def run(
        self,
        classified_complaints: list[ClassifiedComplaint],
        force: bool = False,
        force_from: str | None = None,
        min_cluster_size: int | None = None,
        period_covered: str | None = None,
    ) -> dict:
        """
        Returns the full assembled report dict (also written to
        outputs/opspilot_report.json).
        """
        force_stages = self._resolve_forced_stages(force, force_from)

        root_caused = self._run_root_cause(classified_complaints, force_stages)
        patterns = self._run_pattern_detection(root_caused, force_stages, min_cluster_size)
        recommendations = self._run_recommendations(patterns, force_stages)
        stories = self._run_stories(recommendations, force_stages)
        summary = self._run_summary(patterns, recommendations, len(classified_complaints), force_stages, period_covered)

        report = {
            "executive_summary": summary.model_dump(),
            "patterns": [p.model_dump() for p in patterns],
            "recommendations": [r.model_dump() for r in recommendations],
            "jira_stories": [s.model_dump() for s in stories],
            "stats": {
                "total_complaints_analyzed": len(classified_complaints),
                "total_root_caused": len(root_caused),
                "total_patterns": len(patterns),
                "total_recommendations": len(recommendations),
                "total_stories": len(stories),
            },
        }
        self.paths.final_report.write_text(json.dumps(report, indent=2, default=str))
        logger.info("Phase 3 pipeline complete. Full report -> %s", self.paths.final_report)
        return report

    # -- stage runners ----------------------------------------------------

    def _run_root_cause(self, complaints: list[ClassifiedComplaint], force_stages: set[str]) -> list[RootCausedComplaint]:
        if self.paths.root_caused.exists() and "root_cause" not in force_stages:
            logger.info("Skipping Agent 2 (root cause) -- checkpoint found at %s", self.paths.root_caused)
            return self._load_root_caused(self.paths.root_caused)

        logger.info("Running Agent 2 (root cause) on %d complaints...", len(complaints))
        agent = RootCauseAgent(model_key=self.model_key)
        return agent.analyze_batch(
            complaints, checkpoint_path=self.paths.root_caused, failed_path=self.paths.root_cause_failed
        )

    def _run_pattern_detection(
        self, root_caused: list[RootCausedComplaint], force_stages: set[str], min_cluster_size: int | None
    ) -> list[Pattern]:
        if self.paths.patterns.exists() and "patterns" not in force_stages:
            logger.info("Skipping Agent 3 (pattern detection) -- checkpoint found at %s", self.paths.patterns)
            return [Pattern(**row) for row in json.loads(self.paths.patterns.read_text())]

        logger.info("Running Agent 3 (pattern detection) on %d root-caused complaints...", len(root_caused))
        kwargs = {"model_key": self.model_key}
        if min_cluster_size is not None:
            kwargs["min_cluster_size"] = min_cluster_size
        agent = PatternDetectionAgent(**kwargs)
        patterns = agent.detect(root_caused)
        self.paths.patterns.write_text(json.dumps([p.model_dump() for p in patterns], indent=2, default=str))
        return patterns

    def _run_recommendations(self, patterns: list[Pattern], force_stages: set[str]) -> list[Recommendation]:
        if self.paths.recommendations.exists() and "recommendations" not in force_stages:
            logger.info("Skipping Agent 4 (recommendations) -- checkpoint found at %s", self.paths.recommendations)
            return [Recommendation(**row) for row in json.loads(self.paths.recommendations.read_text())]

        logger.info("Running Agent 4 (recommendations) on %d patterns...", len(patterns))
        agent = RecommendationAgent(model_key=self.model_key)
        recommendations = agent.recommend_batch(patterns)
        self.paths.recommendations.write_text(json.dumps([r.model_dump() for r in recommendations], indent=2, default=str))
        return recommendations

    def _run_stories(self, recommendations: list[Recommendation], force_stages: set[str]) -> list[JiraStory]:
        if self.paths.stories.exists() and "stories" not in force_stages:
            logger.info("Skipping Agent 5 (PM stories) -- checkpoint found at %s", self.paths.stories)
            return [JiraStory(**row) for row in json.loads(self.paths.stories.read_text())]

        logger.info("Running Agent 5 (PM stories) on %d recommendations...", len(recommendations))
        agent = PMAgent(model_key=self.model_key)
        stories = agent.story_batch(recommendations)
        self.paths.stories.write_text(json.dumps([s.model_dump() for s in stories], indent=2, default=str))
        return stories

    def _run_summary(
        self, patterns: list[Pattern], recommendations: list[Recommendation],
        total_complaints: int, force_stages: set[str], period_covered: str | None,
    ) -> ExecutiveSummary:
        if self.paths.summary.exists() and "summary" not in force_stages:
            logger.info("Skipping Agent 6 (executive summary) -- checkpoint found at %s", self.paths.summary)
            return ExecutiveSummary(**json.loads(self.paths.summary.read_text()))

        logger.info("Running Agent 6 (executive summary)...")
        agent = ExecSummaryAgent(model_key=self.model_key)
        summary = agent.summarize(
            patterns=patterns,
            recommendations=recommendations,
            total_complaints_analyzed=total_complaints,
            period_covered=period_covered,
        )
        self.paths.summary.write_text(json.dumps(summary.model_dump(), indent=2, default=str))
        return summary

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _resolve_forced_stages(force: bool, force_from: str | None) -> set[str]:
        if force:
            return set(STAGE_ORDER)
        if force_from:
            if force_from not in STAGE_ORDER:
                raise ValueError(f"force_from must be one of {STAGE_ORDER}, got '{force_from}'")
            idx = STAGE_ORDER.index(force_from)
            return set(STAGE_ORDER[idx:])
        return set()

    @staticmethod
    def _load_root_caused(csv_path: Path) -> list[RootCausedComplaint]:
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


def _load_classified_complaints(csv_path: Path) -> list[ClassifiedComplaint]:
    df = pd.read_csv(csv_path)
    df = df.where(pd.notnull(df), None)
    return [ClassifiedComplaint(**row) for row in df.to_dict(orient="records")]


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 3 -- Multi-Agent Workflow Orchestrator")
    parser.add_argument("--input", type=Path, default=Path("outputs/classified_complaints.csv"),
                         help="Phase 2's classified_complaints.csv")
    parser.add_argument("--output-dir", type=Path, default=Path("outputs"))
    parser.add_argument("--model", type=str, default=None, help="Force a MODEL_REGISTRY key for all stages")
    parser.add_argument("--min-cluster-size", type=int, default=None)
    parser.add_argument("--period", type=str, default=None, help="Human-readable period label, e.g. 'Q2 2026'")
    parser.add_argument("--force", action="store_true", help="Rerun every stage, ignoring existing checkpoints")
    parser.add_argument("--force-from", type=str, default=None, choices=STAGE_ORDER,
                         help="Rerun this stage and every stage after it")
    args = parser.parse_args()

    complaints = _load_classified_complaints(args.input)
    logger.info("Loaded %d classified complaints from %s", len(complaints), args.input)

    pipeline = Phase3Pipeline(model_key=args.model, paths=PipelinePaths(output_dir=args.output_dir))
    pipeline.run(
        complaints,
        force=args.force,
        force_from=args.force_from,
        min_cluster_size=args.min_cluster_size,
        period_covered=args.period,
    )


if __name__ == "__main__":
    main()
