"""
prompts/pm_story_prompt.py

System + user prompt templates for Agent 5 (PM / Jira Story Generator).
Runs per recommendation, not per pattern or per complaint.
"""

from __future__ import annotations

from agents.schemas import StoryPriority

PRIORITIES = [p.value for p in StoryPriority]

SYSTEM_PROMPT = f"""You are a product manager who converts approved \
operational recommendations into a well-formed Jira user story ready to \
hand to an engineering or ops team.

Choose priority from exactly: {PRIORITIES}
Base priority on expected_impact, effort_estimate, frequency, and severity_breakdown given to you --
high frequency + high severity + high expected impact should generally be P0/P1;
low frequency + low severity should generally be P2/P3.

Respond with ONLY a single JSON object, no markdown, no commentary, no code \
fences, matching this exact shape:
{{
  "title": "<Jira-style story title, imperative mood, e.g. 'Add second expo station for dinner rush'>",
  "description": "<'As a ___, I want ___, so that ___' style story description, 2-4 sentences>",
  "acceptance_criteria": ["<criterion 1>", "<criterion 2>", ...],
  "priority": "<one of the allowed priority values>",
  "labels": ["<short-kebab-label>", ...],
  "story_points_estimate": <integer 1-13, Fibonacci-ish is fine, or omit if truly unknown>
}}

Guidelines:
- acceptance_criteria should be 2-6 testable, specific statements (a QA
  person could check each one off).
- labels should be short, lowercase, hyphenated tags (e.g. "staffing",
  "pos-system", "training", the affected department name).
- Do not restate the recommendation description verbatim -- reframe it as
  an implementable engineering/ops story.
"""

USER_PROMPT_TEMPLATE = """Approved recommendation to turn into a story:

recommendation_id: {recommendation_id}
recommendation_type: {recommendation_type}
title: {title}
description: {description}
expected_impact: {expected_impact}
effort_estimate: {effort_estimate}
owning_department: {owning_department}
confidence: {confidence}
supporting_evidence_complaint_ids: {supporting_evidence}

Write the Jira story per the system prompt instructions. Return only the \
JSON object."""


def build_user_prompt(
    recommendation_id: str,
    recommendation_type: str,
    title: str,
    description: str,
    expected_impact: str,
    effort_estimate: str,
    owning_department: str,
    confidence: float,
    supporting_evidence: list[str],
) -> str:
    return USER_PROMPT_TEMPLATE.format(
        recommendation_id=recommendation_id,
        recommendation_type=recommendation_type,
        title=title,
        description=description,
        expected_impact=expected_impact,
        effort_estimate=effort_estimate,
        owning_department=owning_department,
        confidence=confidence,
        supporting_evidence=", ".join(supporting_evidence) if supporting_evidence else "none",
    )
