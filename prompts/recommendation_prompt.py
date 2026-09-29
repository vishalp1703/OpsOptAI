"""
prompts/recommendation_prompt.py

System + user prompt templates for Agent 4 (Recommendation Engine).
Runs per detected pattern, not per complaint.
"""

from __future__ import annotations

from agents.schemas import Department, ImpactLevel, RecommendationType

REC_TYPES = [t.value for t in RecommendationType]
IMPACT_LEVELS = [i.value for i in ImpactLevel]
DEPARTMENTS = [d.value for d in Department]

SYSTEM_PROMPT = f"""You are an operations improvement consultant. You are \
given a confirmed recurring pattern of customer complaints, including its \
diagnosed root cause. Propose ONE concrete, actionable recommendation to \
fix the underlying cause -- not a vague platitude like "improve training" \
with no specifics.

Choose recommendation_type from exactly: {REC_TYPES}
Choose expected_impact and effort_estimate from exactly: {IMPACT_LEVELS}
Choose owning_department from exactly: {DEPARTMENTS}

Respond with ONLY a single JSON object, no markdown, no commentary, no code \
fences, matching this exact shape:
{{
  "recommendation_type": "<one of the allowed types>",
  "title": "<short actionable title, e.g. 'Add second expo station during 6-8pm peak'>",
  "description": "<concrete description of the fix and how it addresses the root cause>",
  "expected_impact": "<Low|Medium|High>",
  "effort_estimate": "<Low|Medium|High>",
  "owning_department": "<one of the allowed departments>",
  "confidence": <float between 0.0 and 1.0>
}}

Guidelines:
- The fix must directly address the pattern's root_cause_category, not just
  the symptom category.
- "effort_estimate" should reflect real-world implementation cost (staffing
  changes are usually Medium+; a policy memo is usually Low; new hardware
  or software is usually Medium-High).
- Be specific enough that a manager could hand this to someone and have
  them know what to actually do.
"""

USER_PROMPT_TEMPLATE = """Pattern to address:

pattern_id: {pattern_id}
title: {title}
description: {description}
root_cause_category: {root_cause_category}
category: {category}
department: {department}
frequency: {frequency} complaints
stores_affected: {stores_affected}
severity_breakdown: {severity_breakdown}
trend: {trend}

Propose the single highest-leverage recommendation per the system prompt \
instructions. Return only the JSON object."""


def build_user_prompt(
    pattern_id: str,
    title: str,
    description: str,
    root_cause_category: str,
    category: str,
    department: str,
    frequency: int,
    stores_affected: list[str],
    severity_breakdown: dict,
    trend: str,
) -> str:
    return USER_PROMPT_TEMPLATE.format(
        pattern_id=pattern_id,
        title=title,
        description=description,
        root_cause_category=root_cause_category,
        category=category,
        department=department,
        frequency=frequency,
        stores_affected=", ".join(stores_affected) if stores_affected else "unknown",
        severity_breakdown=severity_breakdown,
        trend=trend,
    )
