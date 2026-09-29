"""
agents/model_selector.py

Shared logic for resolving "which model do I actually call" -- used by
every Phase 3 agent (root cause, pattern detection, recommendation, PM
story, executive summary) so the selection policy lives in exactly one
place instead of five.

Resolution order:
  1. Explicit `model_key` argument (e.g. "gemini") passed by the caller.
  2. outputs/best_model.json, written by agents/model_evaluator.py (Phase 2)
     -- read defensively since we don't own that file's exact schema here.
  3. FALLBACK_DEFAULT_MODEL from agents/config.py.

NOTE: agents/classification_agent.py (Phase 2) likely already has an
inline version of this same logic. If so, it's worth pointing it at this
module during a later cleanup pass so Phase 2 and Phase 3 can never drift
on model-selection policy. Not required for Phase 3 to function -- just
flagging the duplication for future you.
"""

from __future__ import annotations

import json
import logging

from agents.config import (
    BEST_MODEL_FILE,
    FALLBACK_DEFAULT_MODEL,
    MODEL_REGISTRY,
    ModelSpec,
)

logger = logging.getLogger(__name__)

# outputs/best_model.json's exact key name isn't pinned by contract, so we
# check the most likely candidates rather than assuming one and breaking
# silently if model_evaluator.py names it differently.
_POSSIBLE_KEYS = ("best_model", "winner", "provider", "model_key", "best_provider")


def resolve_model(model_key: str | None = None) -> ModelSpec:
    """
    Return the ModelSpec to use for an LLM call.

    Args:
        model_key: explicit override, e.g. "claude". If given, it must
            exist in MODEL_REGISTRY or this raises KeyError immediately
            (fail loud on a typo rather than silently falling back).
    """
    if model_key:
        if model_key not in MODEL_REGISTRY:
            raise KeyError(
                f"Unknown model_key '{model_key}'. Valid keys: {list(MODEL_REGISTRY)}"
            )
        return MODEL_REGISTRY[model_key]

    if BEST_MODEL_FILE.exists():
        try:
            data = json.loads(BEST_MODEL_FILE.read_text())
            winner_key = next((data[k] for k in _POSSIBLE_KEYS if data.get(k)), None)
            if winner_key and winner_key in MODEL_REGISTRY:
                logger.info("model_selector: using benchmarked best model '%s'", winner_key)
                return MODEL_REGISTRY[winner_key]
            if winner_key:
                logger.warning(
                    "model_selector: best_model.json points at '%s', which isn't "
                    "in MODEL_REGISTRY; falling back to default.", winner_key,
                )
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(
                "model_selector: could not read %s (%s); falling back to default.",
                BEST_MODEL_FILE, exc,
            )

    logger.info("model_selector: using fallback default model '%s'", FALLBACK_DEFAULT_MODEL)
    return MODEL_REGISTRY[FALLBACK_DEFAULT_MODEL]
