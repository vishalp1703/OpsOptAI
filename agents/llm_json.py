"""
agents/llm_json.py

Shared helper: call an LLM via OpenRouter and parse+validate its response
into a Pydantic model, re-prompting on malformed JSON or schema violations.
Mirrors the "never silently drop a row" philosophy of Phase 2's
classification_agent.py so every later agent handles LLM flakiness the
same way instead of reinventing retry/parse logic five times.
"""

from __future__ import annotations

import json
import logging
from typing import Type, TypeVar

from pydantic import BaseModel, ValidationError

from agents.openrouter_client import (
    OpenRouterError,
    OpenRouterTransientError,
    call_model,
)

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class LLMParseError(RuntimeError):
    """Raised when the LLM's output could not be parsed into the target
    schema after all re-prompt attempts are exhausted."""


def _extract_json_block(text: str) -> str:
    """
    Best-effort extraction of a JSON object from LLM output that may be
    wrapped in markdown code fences or preceded by chatty preamble, even
    though we request response_format=json_object -- not every model on
    OpenRouter honors that reliably.
    """
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    if not text.startswith("{"):
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            text = text[start : end + 1]

    return text


def call_and_parse(
    model_slug: str,
    system_prompt: str,
    user_prompt: str,
    result_model: Type[T],
    temperature: float = 0.2,
    max_tokens: int = 700,
    max_reprompts: int = 2,
) -> tuple[T, str]:
    """
    Call `model_slug`, extract JSON from its response, and validate it
    against `result_model`. On malformed JSON or a schema violation,
    re-prompts (up to `max_reprompts` extra times) with the specific error
    appended so the model can self-correct.

    Returns:
        (validated_instance, model_slug_that_actually_served_it)

    Raises:
        LLMParseError: all attempts exhausted without a valid parse.
        OpenRouterError / OpenRouterTransientError: propagated as-is --
            an API/network failure is not a parse failure, so callers can
            tell the two apart and decide how to handle each (e.g. skip
            this item vs. abort the whole batch).
    """
    last_error: str | None = None
    current_user_prompt = user_prompt

    for attempt in range(max_reprompts + 1):
        if last_error:
            current_user_prompt = (
                f"{user_prompt}\n\n"
                f"Your previous response was invalid: {last_error}\n"
                f"Return ONLY a single valid JSON object matching the required "
                f"schema. No markdown, no code fences, no commentary."
            )

        response = call_model(
            model_slug=model_slug,
            system_prompt=system_prompt,
            user_prompt=current_user_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            request_json_mode=True,
        )

        raw = _extract_json_block(response.text)
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            last_error = f"Response was not valid JSON ({exc})."
            logger.warning(
                "call_and_parse attempt %d/%d: JSON decode failed: %s",
                attempt + 1, max_reprompts + 1, last_error,
            )
            continue

        try:
            instance = result_model.model_validate(payload)
        except ValidationError as exc:
            last_error = f"JSON did not match required schema: {exc.errors()[:3]}"
            logger.warning(
                "call_and_parse attempt %d/%d: schema validation failed: %s",
                attempt + 1, max_reprompts + 1, last_error,
            )
            continue

        return instance, response.model

    raise LLMParseError(
        f"Failed to get a valid {result_model.__name__} after "
        f"{max_reprompts + 1} attempts. Last error: {last_error}"
    )
