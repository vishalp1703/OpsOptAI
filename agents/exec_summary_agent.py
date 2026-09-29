"""
agents/exec_summary_agent.py

Agent 6 -- Executive Summary.

Runs once at the end of the pipeline, synthesizing all detected patterns
and generated recommendations into a single leadership-readable summary.
One LLM call for the whole batch (by design -- this is meant to read like
a single coherent narrative, not a per-item report).

CLI:
    python -m agents.exec_summary_agent \\
        --patterns outputs/patterns.json \\
        --recommendations outputs/recommendations.json \\
        --output outputs/executive_summary.json
"""

from __future__ import annotations

import argparse
import json
import logging
import uuid
from pathlib import Path

import pandas as pd

from agents.config import ModelSpec
from agents.llm_json import call_and_parse
from agents.model_selector import resolve_model
from agents.schemas import ExecutiveSummary, ExecutiveSummaryLLMOutput, Pattern, Recommendation
from prompts.executive_summary_prompt import SYSTEM_PROMPT, build_user_prompt

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

# Cap how many patterns/recommendations get summarized into the prompt so
# the exec summary stays a synthesis, not a context-window dump.
TOP_N_PATTERNS = 10
TOP_N_RECOMMENDATIONS = 10


class ExecSummaryAgent:
    def __init__(self, model_key: str | None = None):
        self.model_spec: ModelSpec = resolve_model(model_key)
        logger.info("ExecSummaryAgent using model: %s (%s)", self.model_spec.label, self.model_spec.slug)

    def summarize(
        self,
        patterns: list[Pattern],
        recommendations: list[Recommendation],
        total_complaints_analyzed: int,
        period_covered: str | None = None,
    ) -> ExecutiveSummary:
        top_patterns = sorted(patterns, key=lambda p: p.frequency, reverse=True)[:TOP_N_PATTERNS]
        pattern_summaries = [
            f"[{p.pattern_id}] {p.title} -- {p.frequency} complaints across "
            f"{len(p.stores_affected)} store(s), trend: {p.trend.value}, "
            f"severity breakdown: {p.severity_breakdown}"
            for p in top_patterns
        ]

        rec_by_pattern = {r.pattern_id: r for r in recommendations}
        recommendation_summaries = [
            f"[{rec_by_pattern[p.pattern_id].recommendation_id}] "
            f"({rec_by_pattern[p.pattern_id].recommendation_type.value}, "
            f"impact={rec_by_pattern[p.pattern_id].expected_impact.value}) "
            f"{rec_by_pattern[p.pattern_id].title}"
            for p in top_patterns
            if p.pattern_id in rec_by_pattern
        ][:TOP_N_RECOMMENDATIONS]

        user_prompt = build_user_prompt(
            total_complaints=total_complaints_analyzed,
            total_patterns=len(patterns),
            total_recommendations=len(recommendations),
            period_covered=period_covered,
            pattern_summaries=pattern_summaries,
            recommendation_summaries=recommendation_summaries,
        )
        llm_output, served_by = call_and_parse(
            model_slug=self.model_spec.slug,
            system_prompt=SYSTEM_PROMPT,
            user_prompt=user_prompt,
            result_model=ExecutiveSummaryLLMOutput,
            max_tokens=900,
        )
        return ExecutiveSummary.from_llm_output(
            summary_id=f"SUMMARY-{uuid.uuid4().hex[:8]}",
            llm_output=llm_output,
            model_used=served_by,
            total_complaints_analyzed=total_complaints_analyzed,
            total_patterns_detected=len(patterns),
            total_recommendations=len(recommendations),
            period_covered=period_covered,
        )


def _load_patterns(json_path: Path) -> list[Pattern]:
    return [Pattern(**row) for row in json.loads(json_path.read_text())]


def _load_recommendations(json_path: Path) -> list[Recommendation]:
    return [Recommendation(**row) for row in json.loads(json_path.read_text())]


def main() -> None:
    parser = argparse.ArgumentParser(description="Agent 6 -- Executive Summary")
    parser.add_argument("--patterns", type=Path, default=Path("outputs/patterns.json"))
    parser.add_argument("--recommendations", type=Path, default=Path("outputs/recommendations.json"))
    parser.add_argument("--total-complaints", type=int, required=True)
    parser.add_argument("--period", type=str, default=None)
    parser.add_argument("--output", type=Path, default=Path("outputs/executive_summary.json"))
    parser.add_argument("--model", type=str, default=None)
    args = parser.parse_args()

    patterns = _load_patterns(args.patterns)
    recommendations = _load_recommendations(args.recommendations)

    agent = ExecSummaryAgent(model_key=args.model)
    summary = agent.summarize(
        patterns=patterns,
        recommendations=recommendations,
        total_complaints_analyzed=args.total_complaints,
        period_covered=args.period,
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary.model_dump(), indent=2, default=str))
    logger.info("Wrote executive summary -> %s", args.output)


if __name__ == "__main__":
    main()
