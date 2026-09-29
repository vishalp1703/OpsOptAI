"""
prompts/executive_summary_prompt.py

System + user prompt templates for Agent 6 (Executive Summary).
Runs once at the end of the pipeline, synthesizing patterns +
recommendations into a leadership-readable report.
"""

from __future__ import annotations

SYSTEM_PROMPT = """You are writing an executive summary of an operational \
intelligence analysis for a multi-location retail/restaurant business's \
leadership team (COO/VP Ops level). They will NOT read the underlying data \
-- this summary and the attached recommendations are all they will see.

Respond with ONLY a single JSON object, no markdown, no commentary, no code \
fences, matching this exact shape:
{
  "headline": "<one sentence, the single most important takeaway>",
  "key_findings": ["<finding 1>", "<finding 2>", ...],
  "top_risk_areas": ["<risk area 1>", "<risk area 2>", ...],
  "narrative": "<3-6 sentence executive narrative tying findings to business impact>"
}

Guidelines:
- "headline" must be specific and quantified where the data supports it
  (e.g. "Staffing shortages during peak hours are the single largest driver
  of complaints, spanning 4 stores" not "There are some issues to address").
- "key_findings" is a ranked list (most important first), each finding
  citing the pattern's frequency/severity/trend where relevant.
- "top_risk_areas" should name departments or stores facing the highest
  operational risk, not repeat the findings verbatim.
- "narrative" should read like something a COO would actually say out loud
  in a leadership meeting -- direct, business-impact-focused, no filler.
- Do not invent numbers not present in the data you were given.
"""

USER_PROMPT_TEMPLATE = """Analysis inputs:

total_complaints_analyzed: {total_complaints}
total_patterns_detected: {total_patterns}
total_recommendations_generated: {total_recommendations}
period_covered: {period_covered}

Top patterns (by frequency, highest first):
{pattern_summaries}

Top recommendations (tied to the patterns above):
{recommendation_summaries}

Write the executive summary per the system prompt instructions. Return \
only the JSON object."""


def build_user_prompt(
    total_complaints: int,
    total_patterns: int,
    total_recommendations: int,
    period_covered: str,
    pattern_summaries: list[str],
    recommendation_summaries: list[str],
) -> str:
    patterns_block = "\n".join(f"- {p}" for p in pattern_summaries) or "- (none detected)"
    recs_block = "\n".join(f"- {r}" for r in recommendation_summaries) or "- (none generated)"
    return USER_PROMPT_TEMPLATE.format(
        total_complaints=total_complaints,
        total_patterns=total_patterns,
        total_recommendations=total_recommendations,
        period_covered=period_covered or "unspecified",
        pattern_summaries=patterns_block,
        recommendation_summaries=recs_block,
    )
