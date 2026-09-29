"""
agents/config.py

Central configuration for OpenRouter access and the candidate model
registry used by both the classification agent and the model evaluator.

IMPORTANT: OpenRouter's catalogue and pricing change frequently. The
MODEL_REGISTRY below is a starting point, not gospel. Before relying on
results, run:

    python -m agents.model_evaluator --check-slugs

which pings OpenRouter's live /models endpoint and flags any slug below
that no longer exists, so you're never silently benchmarking a dead model.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root regardless of where this module is imported from
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Sent as OpenRouter's recommended attribution headers. Not required, but
# improves your app's standing in their analytics/rate-limit tiers.
OPENROUTER_SITE_URL = os.getenv("OPENROUTER_SITE_URL", "https://opspilot.local")
OPENROUTER_APP_NAME = os.getenv("OPENROUTER_APP_NAME", "OpsPilot-AI")

if not OPENROUTER_API_KEY:
    # Don't crash at import time (e.g. during `pytest --collect-only`), but
    # make it loud so it's impossible to miss when something actually tries
    # to call the API.
    print(
        "[agents.config] WARNING: OPENROUTER_API_KEY is not set. "
        "Copy .env.example to .env and fill in your key."
    )


@dataclass(frozen=True)
class ModelSpec:
    slug: str                    # exact OpenRouter model id, e.g. "openai/gpt-4o-mini"
    label: str                   # friendly name for reports/logs
    provider: str                # gpt / claude / gemini / deepseek / grok
    input_cost_per_m: float      # USD per 1M input tokens (approximate)
    output_cost_per_m: float     # USD per 1M output tokens (approximate)
    supports_json_mode: bool = True


# ---------------------------------------------------------------------------
# Candidate models for Agent 1 classification, one per family requested in
# the spec (GPT, Claude, Gemini, DeepSeek, Grok). Cost-efficient mid/small
# tier picked for each family since classification is a high-volume, low-
# reasoning task -- no need for flagship pricing here.
#
# ⚠️ VERIFY SLUGS: run `python -m agents.model_evaluator --check-slugs`
# after cloning this before your first real benchmark run. OpenRouter slugs
# and pricing drift over time; the values below were current as of this
# writing but you should treat them as defaults to confirm, not constants.
# ---------------------------------------------------------------------------
MODEL_REGISTRY: dict[str, ModelSpec] = {
    "gpt": ModelSpec(
        slug="openai/gpt-4o-mini",
        label="GPT-4o mini",
        provider="gpt",
        input_cost_per_m=0.15,
        output_cost_per_m=0.60,
    ),
    "claude": ModelSpec(
        slug="anthropic/claude-haiku-4.5",
        label="Claude Haiku 4.5",
        provider="claude",
        input_cost_per_m=1.00,
        output_cost_per_m=5.00,
    ),
    "gemini": ModelSpec(
        slug="google/gemini-2.5-flash",
        label="Gemini 2.5 Flash",
        provider="gemini",
        input_cost_per_m=0.30,
        output_cost_per_m=2.50,
    ),
    "deepseek": ModelSpec(
        slug="deepseek/deepseek-chat",
        label="DeepSeek V3 Chat",
        provider="deepseek",
        input_cost_per_m=0.27,
        output_cost_per_m=1.10,
    ),
    "grok": ModelSpec(
        slug="x-ai/grok-4.3",  # grok-4-fast was deprecated by xAI; this replaces it
        label="Grok 4.3",
        provider="grok",
        input_cost_per_m=0.20,   # placeholder -- verify against openrouter.ai/models before trusting cost rankings
        output_cost_per_m=0.50,  # placeholder -- verify against openrouter.ai/models before trusting cost rankings
    ),
}

# Used by classification_agent.py when no model is explicitly passed and no
# outputs/best_model.json (written by the evaluator) exists yet.
FALLBACK_DEFAULT_MODEL = "gpt"

# Where the evaluator writes its recommendation so the classification agent
# can automatically pick it up on subsequent runs.
BEST_MODEL_FILE = PROJECT_ROOT / "outputs" / "best_model.json"

REQUEST_TIMEOUT_SECONDS = 30
MAX_RETRIES = 3
