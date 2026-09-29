"""
agents/recommendation_agent.py

Agent 4 -- Recommendation Engine.

Runs once per detected pattern (Agent 3's output), proposing a concrete
operational/product/training fix tied back to that pattern's root cause,
with evidence (a sample of the pattern's complaint_ids) attached.

CLI:
    python -m agents.recommendation_agent \\
        --input outputs/patterns.json \\
        --output outputs/recommendations.json
"""

from __future__ import annotations

import argparse
import json
import logging
import uuid
from pathlib import Path

from agents.config import ModelSpec
from agents.llm_json import LLMParseError, call_and_parse
from agents.model_selector import resolve_model
from agents.openrouter_client import OpenRouterError, OpenRouterTransientError
from agents.schemas import Pattern, Recommendation, RecommendationLLMOutput
from prompts.recommendation_prompt import SYSTEM_PROMPT, build_user_prompt

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)


class RecommendationAgent:
    def __init__(self, model_key: str | None = None):
        self.model_spec: ModelSpec = resolve_model(model_key)
        logger.info("RecommendationAgent using model: %s (%s)", self.model_spec.label, self.model_spec.slug)

    def recommend_for_pattern(self, pattern: Pattern) -> Recommendation:
        user_prompt = build_user_prompt(
            pattern_id=pattern.pattern_id,
            title=pattern.title,
            description=pattern.description,
            root_cause_category=pattern.root_cause_category.value,
            category=pattern.category.value,
            department=pattern.department.value,
            frequency=pattern.frequency,
            stores_affected=pattern.stores_affected,
            severity_breakdown=pattern.severity_breakdown,
            trend=pattern.trend.value,
        )
        llm_output, served_by = call_and_parse(
            model_slug=self.model_spec.slug,
            system_prompt=SYSTEM_PROMPT,
            user_prompt=user_prompt,
            result_model=RecommendationLLMOutput,
        )
        return Recommendation.from_pattern_and_result(
            pattern=pattern,
            recommendation_id=f"REC-{uuid.uuid4().hex[:8]}",
            llm_output=llm_output,
            model_used=served_by,
        )

    def recommend_batch(self, patterns: list[Pattern]) -> list[Recommendation]:
        recommendations: list[Recommendation] = []
        for pattern in patterns:
            try:
                recommendations.append(self.recommend_for_pattern(pattern))
            except (LLMParseError, OpenRouterError, OpenRouterTransientError) as exc:
                logger.error("Recommendation generation failed for pattern_id=%s: %s", pattern.pattern_id, exc)
        logger.info(
            "Recommendation generation complete: %d/%d patterns succeeded.",
            len(recommendations), len(patterns),
        )
        return recommendations


def _load_patterns(json_path: Path) -> list[Pattern]:
    data = json.loads(json_path.read_text())
    return [Pattern(**row) for row in data]


def main() -> None:
    parser = argparse.ArgumentParser(description="Agent 4 -- Recommendation Engine")
    parser.add_argument("--input", type=Path, default=Path("outputs/patterns.json"))
    parser.add_argument("--output", type=Path, default=Path("outputs/recommendations.json"))
    parser.add_argument("--model", type=str, default=None)
    args = parser.parse_args()

    patterns = _load_patterns(args.input)
    logger.info("Loaded %d patterns from %s", len(patterns), args.input)

    agent = RecommendationAgent(model_key=args.model)
    recommendations = agent.recommend_batch(patterns)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps([r.model_dump() for r in recommendations], indent=2, default=str))
    logger.info("Wrote %d recommendations -> %s", len(recommendations), args.output)


if __name__ == "__main__":
    main()
