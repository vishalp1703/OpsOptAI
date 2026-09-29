"""
prompts/classification_prompt.py

Prompt template for Agent 1 (Classification). Kept separate from the agent
logic so prompts can be iterated on / A-B tested without touching code.

The schema/allowed values here MUST stay in sync with agents/schemas.py.
If you add a Category or Department there, add it to the lists below too.
"""

from agents.schemas import Category, Department, Sentiment, Severity

_CATEGORY_LIST = ", ".join(c.value for c in Category)
_DEPARTMENT_LIST = ", ".join(d.value for d in Department)
_SENTIMENT_LIST = ", ".join(s.value for s in Sentiment)
_SEVERITY_LIST = ", ".join(s.value for s in Severity)


SYSTEM_PROMPT = f"""You are an operational intelligence analyst for a multi-location retail/restaurant business.
Your job is to read a single piece of customer or employee feedback (a complaint, review, ticket, or note) and classify it precisely.

You must respond with ONLY a single valid JSON object. No markdown, no code fences, no commentary before or after.

The JSON object must have exactly these keys:
{{
  "category": one of [{_CATEGORY_LIST}],
  "sentiment": one of [{_SENTIMENT_LIST}],
  "severity": one of [{_SEVERITY_LIST}],
  "department": one of [{_DEPARTMENT_LIST}],
  "confidence": a float between 0.0 and 1.0 representing how confident you are in this classification,
  "reasoning": a single short sentence (max 25 words) explaining your reasoning
}}

Guidance:
- "severity" reflects operational/business risk, not just how upset the customer sounds. A calmly-worded food safety issue is Critical; an angry complaint about slow condiment refills is Low.
- "department" is who inside the company should own fixing the root cause, not who received the complaint.
- "confidence" should genuinely reflect ambiguity. If the text is vague or could fit two categories, say so with a lower score (e.g. 0.4-0.6) rather than defaulting to 0.9.
- If the text mentions multiple issues, classify based on the PRIMARY/most severe issue.
- Never invent information that isn't in the text.
"""


def build_user_prompt(customer_text: str, store: str | None = None, source: str | None = None) -> str:
    """Build the user-turn prompt for a single complaint."""
    context_lines = []
    if store:
        context_lines.append(f"Store/Location: {store}")
    if source:
        context_lines.append(f"Source: {source}")
    context_block = ("\n".join(context_lines) + "\n") if context_lines else ""

    return (
        f"{context_block}"
        f"Feedback text:\n\"\"\"\n{customer_text}\n\"\"\"\n\n"
        "Return the JSON object now."
    )
