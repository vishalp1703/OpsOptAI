"""
agents/openrouter_client.py

Thin wrapper around OpenRouter's OpenAI-compatible chat completions
endpoint. Kept deliberately dependency-light (just `requests`) so any
agent (classification, root cause, pattern detection, ...) can reuse it
without pulling in a provider-specific SDK.

Responsibilities here, and only here:
  - Auth headers / base URL
  - Retries with exponential backoff on transient network/5xx errors
  - Timing each call (used by the model evaluator for latency scoring)
  - Live model list lookup, for slug validation

Everything prompt-shaped or schema-shaped lives elsewhere.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

import requests
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from agents.config import (
    MAX_RETRIES,
    OPENROUTER_API_KEY,
    OPENROUTER_APP_NAME,
    OPENROUTER_BASE_URL,
    OPENROUTER_SITE_URL,
    REQUEST_TIMEOUT_SECONDS,
)


class OpenRouterError(RuntimeError):
    """Raised for non-retryable OpenRouter failures (bad request, auth, etc.)."""


class OpenRouterTransientError(RuntimeError):
    """Raised for retryable failures (timeouts, 429, 5xx)."""


@dataclass
class LLMResponse:
    text: str                  # raw completion text (may or may not be JSON)
    model: str                 # the model slug that actually served the request
    latency_seconds: float
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": OPENROUTER_SITE_URL,
        "X-Title": OPENROUTER_APP_NAME,
    }


@retry(
    reraise=True,
    stop=stop_after_attempt(MAX_RETRIES),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type(OpenRouterTransientError),
)
def call_model(
    model_slug: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.0,
    max_tokens: int = 400,
    request_json_mode: bool = True,
) -> LLMResponse:
    """
    Call a single model via OpenRouter and return the raw text completion.

    Raises:
        OpenRouterError: for non-retryable failures (401, 400, model not
            found, etc.) -- caller should not retry these.
        OpenRouterTransientError -> retried automatically up to MAX_RETRIES
            times with exponential backoff; if still failing, tenacity
            re-raises the last OpenRouterTransientError to the caller.
    """
    if not OPENROUTER_API_KEY:
        raise OpenRouterError(
            "OPENROUTER_API_KEY is not set. Add it to your .env file."
        )

    payload = {
        "model": model_slug,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    # Not every model on OpenRouter honors response_format reliably, so we
    # still do defensive JSON extraction downstream in the agent -- this is
    # a best-effort nudge, not a guarantee.
    if request_json_mode:
        payload["response_format"] = {"type": "json_object"}

    start = time.monotonic()
    try:
        resp = requests.post(
            f"{OPENROUTER_BASE_URL}/chat/completions",
            headers=_headers(),
            json=payload,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.exceptions.RequestException as exc:
        raise OpenRouterTransientError(f"Network error calling {model_slug}: {exc}") from exc

    latency = time.monotonic() - start

    if resp.status_code == 429 or resp.status_code >= 500:
        raise OpenRouterTransientError(
            f"{model_slug} returned {resp.status_code}: {resp.text[:200]}"
        )
    if resp.status_code != 200:
        raise OpenRouterError(
            f"{model_slug} returned {resp.status_code}: {resp.text[:300]}"
        )

    data = resp.json()
    try:
        choice = data["choices"][0]
        text = choice["message"]["content"]
    except (KeyError, IndexError) as exc:
        raise OpenRouterError(f"Unexpected response shape from {model_slug}: {data}") from exc

    usage = data.get("usage", {})
    return LLMResponse(
        text=text,
        model=data.get("model", model_slug),
        latency_seconds=latency,
        prompt_tokens=usage.get("prompt_tokens"),
        completion_tokens=usage.get("completion_tokens"),
    )


def list_live_models() -> set[str]:
    """
    Query OpenRouter's live model catalogue and return the set of valid
    model slugs. Used to validate MODEL_REGISTRY before benchmarking so we
    fail fast on a renamed/retired slug instead of burning API calls on 404s.
    """
    if not OPENROUTER_API_KEY:
        raise OpenRouterError("OPENROUTER_API_KEY is not set.")

    resp = requests.get(
        f"{OPENROUTER_BASE_URL}/models",
        headers=_headers(),
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    data = resp.json()
    return {item["id"] for item in data.get("data", [])}
