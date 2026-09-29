"""
agents/classification_agent.py

Agent 1 -- Classification.

Takes cleaned complaint rows (Phase 1 output) and, for each one, calls an
LLM via OpenRouter to produce a structured ClassificationResult: category,
sentiment, severity, department, confidence.

Usage (single complaint):

    from agents.classification_agent import ClassificationAgent
    agent = ClassificationAgent()  # uses best/default model
    result = agent.classify_complaint("The fries were cold and staff ignored us for 20 minutes.")

Usage (batch, from CLI):

    python -m agents.classification_agent --input data/cleaned/complaints.csv \
        --output outputs/classified_complaints.csv

If --model is omitted, the agent uses outputs/best_model.json (written by
`python -m agents.model_evaluator`) if present, otherwise falls back to
config.FALLBACK_DEFAULT_MODEL.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
from pydantic import ValidationError

from agents.config import BEST_MODEL_FILE, FALLBACK_DEFAULT_MODEL, MODEL_REGISTRY
from agents.openrouter_client import (
    OpenRouterError,
    OpenRouterTransientError,
    call_model,
)
from agents.schemas import ClassificationResult, ClassifiedComplaint, ComplaintInput
from prompts.classification_prompt import SYSTEM_PROMPT, build_user_prompt

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("classification_agent")

_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json(raw_text: str) -> dict:
    """
    Best-effort extraction of a JSON object from an LLM completion.

    Models occasionally wrap JSON in markdown fences or add stray text even
    when asked not to. Strategy:
      1. Try straight json.loads first (fast path, works most of the time).
      2. Strip common markdown fences and retry.
      3. Regex out the first {...} block and retry.
    Raises json.JSONDecodeError if all strategies fail, so the caller can
    trigger a re-prompt.
    """
    text = raw_text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    stripped = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    match = _JSON_OBJECT_RE.search(text)
    if match:
        return json.loads(match.group(0))  # let this raise if still invalid

    raise json.JSONDecodeError("No JSON object found in model output", text, 0)


def _resolve_model_slug(model_key: Optional[str]) -> tuple[str, str]:
    """
    Resolve a model key ("gpt", "claude", ...) or raw slug to an actual
    OpenRouter slug, returning (slug, friendly_key_used).

    Resolution order if model_key is None:
      1. outputs/best_model.json (written by the evaluator)
      2. config.FALLBACK_DEFAULT_MODEL
    """
    if model_key is None:
        if BEST_MODEL_FILE.exists():
            try:
                best = json.loads(BEST_MODEL_FILE.read_text())
                model_key = best["model_key"]
                logger.info(
                    "No model specified -- using evaluator-recommended model '%s' "
                    "(from %s, scored %.3f)",
                    model_key, BEST_MODEL_FILE.name, best.get("composite_score", float("nan")),
                )
            except (json.JSONDecodeError, KeyError):
                logger.warning("Could not parse %s, falling back to default model.", BEST_MODEL_FILE)
                model_key = FALLBACK_DEFAULT_MODEL
        else:
            model_key = FALLBACK_DEFAULT_MODEL

    if model_key in MODEL_REGISTRY:
        return MODEL_REGISTRY[model_key].slug, model_key

    # Allow passing a raw OpenRouter slug directly (e.g. "openai/gpt-4o")
    return model_key, model_key


class ClassificationAgent:
    def __init__(self, model: Optional[str] = None, max_json_retries: int = 2):
        """
        Args:
            model: key into MODEL_REGISTRY ("gpt", "claude", "gemini",
                "deepseek", "grok"), a raw OpenRouter slug, or None to
                auto-select (see _resolve_model_slug).
            max_json_retries: how many times to re-prompt the model if it
                returns text that isn't parseable/valid JSON matching our
                schema, before giving up on that complaint.
        """
        self.model_slug, self.model_key = _resolve_model_slug(model)
        self.max_json_retries = max_json_retries
        logger.info("ClassificationAgent using model: %s (%s)", self.model_slug, self.model_key)

    def classify_complaint(
        self, customer_text: str, store: Optional[str] = None, source: Optional[str] = None,
        complaint_id: str = "unassigned",
    ) -> ClassificationResult:
        """Classify a single piece of feedback text. Raises OpenRouterError
        if the model cannot be reached, or ValueError if it never returns
        valid, schema-compliant JSON after retries."""
        user_prompt = build_user_prompt(customer_text, store=store, source=source)
        last_error: Optional[Exception] = None

        for attempt in range(1 + self.max_json_retries):
            prompt = user_prompt
            if attempt > 0:
                prompt += (
                    "\n\nYour previous response was not valid JSON matching the required "
                    "schema. Respond with ONLY the raw JSON object, nothing else."
                )
            try:
                response = call_model(
                    model_slug=self.model_slug,
                    system_prompt=SYSTEM_PROMPT,
                    user_prompt=prompt,
                )
            except OpenRouterTransientError as exc:
                # Already retried internally by call_model; treat as fatal for this item.
                raise OpenRouterError(f"Transient failure exhausted retries: {exc}") from exc

            try:
                parsed = _extract_json(response.text)
                parsed["complaint_id"] = complaint_id
                parsed["model_used"] = response.model
                parsed["classified_at"] = datetime.now(timezone.utc).isoformat()
                return ClassificationResult(**parsed)
            except (json.JSONDecodeError, ValidationError) as exc:
                last_error = exc
                logger.warning(
                    "Attempt %d/%d: model returned invalid output for complaint %s: %s",
                    attempt + 1, self.max_json_retries + 1, complaint_id, exc,
                )
                continue

        raise ValueError(
            f"Model {self.model_slug} failed to return valid classification "
            f"for complaint {complaint_id} after {self.max_json_retries + 1} attempts: {last_error}"
        )

    def classify_batch(
        self,
        df: pd.DataFrame,
        text_col: str = "customer_text",
        checkpoint_path: Optional[Path] = None,
        checkpoint_every: int = 25,
    ) -> pd.DataFrame:
        """
        Classify every row of a DataFrame (as produced by Phase 1).

        Failed calls are retained in ``last_failures`` and excluded from the
        classified output. This prevents fabricated fallback labels from
        contaminating trend metrics; the source complaint remains available
        for a later retry.

        Args:
            df: must contain at least `text_col`; complaint_id/source/store/
                date are used if present.
            checkpoint_path: if given, partial progress is written to this
                CSV every `checkpoint_every` rows -- cheap insurance against
                losing a long run to a crash or rate limit wall.
        """
        results: list[dict] = []
        self.last_failures: list[dict] = []
        total = len(df)

        for i, row in enumerate(df.itertuples(index=False), start=1):
            row_dict = row._asdict()
            complaint_id = str(row_dict.get("complaint_id", f"row_{i}"))
            try:
                complaint = ComplaintInput(
                    complaint_id=complaint_id,
                    source=row_dict.get("source"),
                    store=row_dict.get("store"),
                    date=row_dict.get("date"),
                    customer_text=row_dict.get(text_col, ""),
                )
            except ValidationError as exc:
                logger.warning("Skipping row %s -- invalid input: %s", complaint_id, exc)
                continue

            try:
                result = self.classify_complaint(
                    customer_text=complaint.customer_text,
                    store=complaint.store,
                    source=complaint.source,
                    complaint_id=complaint.complaint_id,
                )
                classified = ClassifiedComplaint.from_input_and_result(complaint, result)
                results.append(classified.model_dump(mode="json"))
                logger.info("[%d/%d] %s -> %s / %s (conf %.2f)",
                            i, total, complaint_id, result.category.value,
                            result.severity.value, result.confidence)
            except (OpenRouterError, ValueError) as exc:
                logger.error("[%d/%d] %s FAILED: %s", i, total, complaint_id, exc)
                self.last_failures.append({
                    "complaint_id": complaint.complaint_id,
                    "error": str(exc),
                })

            if checkpoint_path and i % checkpoint_every == 0:
                pd.DataFrame(results).to_csv(checkpoint_path, index=False)
                logger.info("Checkpoint saved (%d/%d rows) -> %s", i, total, checkpoint_path)

        out_df = pd.DataFrame(results)
        if out_df.empty:
            out_df = pd.DataFrame(columns=list(ClassifiedComplaint.model_fields))
        if checkpoint_path:
            out_df.to_csv(checkpoint_path, index=False)
        return out_df


def _cli():
    parser = argparse.ArgumentParser(description="Run Agent 1 (Classification) over a cleaned complaints CSV.")
    parser.add_argument("--input", required=True, help="Path to cleaned CSV from Phase 1")
    parser.add_argument("--output", required=True, help="Path to write classified CSV")
    parser.add_argument("--model", default=None, help="Model key (gpt/claude/gemini/deepseek/grok) or raw OpenRouter slug")
    parser.add_argument("--limit", type=int, default=None, help="Only process the first N rows (useful for testing)")
    args = parser.parse_args()

    df = pd.read_csv(args.input)
    if args.limit:
        df = df.head(args.limit)

    agent = ClassificationAgent(model=args.model)
    out_df = agent.classify_batch(
        df, checkpoint_path=Path(args.output).with_suffix(".checkpoint.csv")
    )
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    out_df.to_csv(args.output, index=False)
    if agent.last_failures:
        failures_path = Path(args.output).with_suffix(".failures.json")
        failures_path.write_text(json.dumps(agent.last_failures, indent=2))
        logger.warning("%d classification failure(s) recorded -> %s", len(agent.last_failures), failures_path)
    logger.info("Done. Wrote %d classified rows to %s", len(out_df), args.output)


if __name__ == "__main__":
    _cli()
