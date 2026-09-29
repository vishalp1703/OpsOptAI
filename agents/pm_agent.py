"""
agents/pm_agent.py

Agent 5 -- PM / Jira Story Generator.

Runs once per approved recommendation (Agent 4's output), converting it
into a structured, engineering-ready user story: title, description,
acceptance criteria, priority, labels, story point estimate.

CLI:
    python -m agents.pm_agent \\
        --input outputs/recommendations.json \\
        --output outputs/jira_stories.json
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
from agents.schemas import JiraStory, JiraStoryLLMOutput, Recommendation
from prompts.pm_story_prompt import SYSTEM_PROMPT, build_user_prompt

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)


class PMAgent:
    def __init__(self, model_key: str | None = None):
        self.model_spec: ModelSpec = resolve_model(model_key)
        logger.info("PMAgent using model: %s (%s)", self.model_spec.label, self.model_spec.slug)

    def story_for_recommendation(self, recommendation: Recommendation) -> JiraStory:
        user_prompt = build_user_prompt(
            recommendation_id=recommendation.recommendation_id,
            recommendation_type=recommendation.recommendation_type.value,
            title=recommendation.title,
            description=recommendation.description,
            expected_impact=recommendation.expected_impact.value,
            effort_estimate=recommendation.effort_estimate.value,
            owning_department=recommendation.owning_department.value,
            confidence=recommendation.confidence,
            supporting_evidence=recommendation.supporting_evidence,
        )
        llm_output, served_by = call_and_parse(
            model_slug=self.model_spec.slug,
            system_prompt=SYSTEM_PROMPT,
            user_prompt=user_prompt,
            result_model=JiraStoryLLMOutput,
        )
        return JiraStory.from_recommendation_and_result(
            recommendation=recommendation,
            story_id=f"STORY-{uuid.uuid4().hex[:8]}",
            llm_output=llm_output,
            model_used=served_by,
        )

    def story_batch(self, recommendations: list[Recommendation]) -> list[JiraStory]:
        stories: list[JiraStory] = []
        for rec in recommendations:
            try:
                stories.append(self.story_for_recommendation(rec))
            except (LLMParseError, OpenRouterError, OpenRouterTransientError) as exc:
                logger.error("Story generation failed for recommendation_id=%s: %s", rec.recommendation_id, exc)
        logger.info("Story generation complete: %d/%d recommendations succeeded.", len(stories), len(recommendations))
        return stories


def _load_recommendations(json_path: Path) -> list[Recommendation]:
    data = json.loads(json_path.read_text())
    return [Recommendation(**row) for row in data]


def main() -> None:
    parser = argparse.ArgumentParser(description="Agent 5 -- PM / Jira Story Generator")
    parser.add_argument("--input", type=Path, default=Path("outputs/recommendations.json"))
    parser.add_argument("--output", type=Path, default=Path("outputs/jira_stories.json"))
    parser.add_argument("--model", type=str, default=None)
    args = parser.parse_args()

    recommendations = _load_recommendations(args.input)
    logger.info("Loaded %d recommendations from %s", len(recommendations), args.input)

    agent = PMAgent(model_key=args.model)
    stories = agent.story_batch(recommendations)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps([s.model_dump() for s in stories], indent=2, default=str))
    logger.info("Wrote %d Jira stories -> %s", len(stories), args.output)


if __name__ == "__main__":
    main()
