"""
prompts/pattern_detection_prompt.py

System + user prompt templates for Agent 3 (Pattern Detection).

Design note: clustering itself (which complaints belong together, the
counts, the severity breakdown, the store list) is computed deterministically
in agents/pattern_detection_agent.py using pandas groupby -- NOT by the LLM.
LLMs are unreliable at exact counting/aggregation over many rows and will
happily hallucinate a plausible-sounding number. The LLM's only job here is
the qualitative part: naming the pattern, writing a human-readable
description, and judging the trend direction from the time-bucketed counts
we hand it as evidence.
"""

from __future__ import annotations

from agents.schemas import TrendDirection

TREND_VALUES = [t.value for t in TrendDirection]

SYSTEM_PROMPT = f"""You are an operations intelligence analyst. You are given \
a pre-computed cluster of customer complaints that share the same category, \
department, and root cause diagnosis -- i.e. this is already confirmed to \
be a RECURRING issue, not a one-off. Your job is to:

1. Write a short, specific title for this pattern (not generic, e.g. \
   "Repeated cold food from expo delays during dinner rush at 3 stores" \
   not "Food Quality Issue").
2. Write a 2-4 sentence description explaining what the recurring issue is \
   and why it matters operationally, grounded in the sample complaints you \
   are shown.
3. Judge the trend using the time-bucketed counts provided: choose exactly \
   one of {TREND_VALUES}. If fewer than 2 time buckets have data, or the \
   counts are too close to call, choose "Insufficient Data" -- do not guess.

Respond with ONLY a single JSON object, no markdown, no commentary, no code \
fences, matching this exact shape:
{{
  "title": "<specific, short pattern title>",
  "description": "<2-4 sentence description>",
  "trend": "<one of the allowed trend values>"
}}
"""

USER_PROMPT_TEMPLATE = """Cluster summary:
category: {category}
department: {department}
root_cause_category: {root_cause_category}
total_complaints_in_cluster: {frequency}
stores_affected: {stores_affected}
severity_breakdown: {severity_breakdown}
time_bucketed_counts (chronological order): {time_buckets}

Sample complaint excerpts from this cluster (up to 5, for grounding -- the \
full cluster is larger than this sample):
{sample_excerpts}

Write the pattern title, description, and trend per the system prompt \
instructions. Return only the JSON object."""


def build_user_prompt(
    category: str,
    department: str,
    root_cause_category: str,
    frequency: int,
    stores_affected: list[str],
    severity_breakdown: dict[str, int],
    time_buckets: list[int],
    sample_excerpts: list[str],
) -> str:
    excerpts_block = "\n".join(f"- \"{e}\"" for e in sample_excerpts) or "- (no text samples available)"
    return USER_PROMPT_TEMPLATE.format(
        category=category,
        department=department,
        root_cause_category=root_cause_category,
        frequency=frequency,
        stores_affected=", ".join(stores_affected) if stores_affected else "unknown",
        severity_breakdown=severity_breakdown,
        time_buckets=time_buckets,
        sample_excerpts=excerpts_block,
    )
