"""
agents/root_cause_agent.py

Agent 2 -- Root Cause Analysis.

Takes Phase 2's classified complaints (category, sentiment, severity,
department, confidence already assigned) and, per complaint, diagnoses the
underlying operational cause. Runs one LLM call per complaint.

Mirrors Phase 2's classification_agent.py operating pattern:
  - checkpoints progress every CHECKPOINT_EVERY rows so a crash mid-batch
    doesn't lose completed work
  - never silently drops a row -- rows that fail after all retries are
    logged and written to a `_failed` sidecar file instead of vanishing
  - auto-resolves the LLM to call via agents/model_selector.py (best_model
    .json if present, else FALLBACK_DEFAULT_MODEL)

CLI:
    python -m agents.root_cause_agent \\
        --input outputs/classified_complaints.csv \\
        --output outputs/root_caused_complaints.csv
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import pandas as pd

from agents.config import ModelSpec
from agents.llm_json import LLMParseError, call_and_parse
from agents.model_selector import resolve_model
from agents.openrouter_client import OpenRouterError, OpenRouterTransientError
from agents.schemas import ClassifiedComplaint, RootCauseLLMOutput, RootCauseResult, RootCausedComplaint
from prompts.root_cause_prompt import SYSTEM_PROMPT, build_user_prompt

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

CHECKPOINT_EVERY = 25


class RootCauseAgent:
    def __init__(self, model_key: str | None = None):
        """
        Args:
            model_key: explicit MODEL_REGISTRY key (e.g. "gemini") to force
                a specific model. If None, auto-resolves via
                agents.model_selector (best_model.json -> fallback).
        """
        self.model_spec: ModelSpec = resolve_model(model_key)
        logger.info("RootCauseAgent using model: %s (%s)", self.model_spec.label, self.model_spec.slug)

    def analyze_one(self, complaint: ClassifiedComplaint) -> RootCauseResult:
        """Diagnose the root cause for a single classified complaint."""
        user_prompt = build_user_prompt(
            complaint_id=complaint.complaint_id,
            category=complaint.category.value,
            sentiment=complaint.sentiment.value,
            severity=complaint.severity.value,
            department=complaint.department.value,
            store=complaint.store,
            date=complaint.date,
            customer_text=complaint.customer_text,
        )
        llm_output, served_by = call_and_parse(
            model_slug=self.model_spec.slug,
            system_prompt=SYSTEM_PROMPT,
            user_prompt=user_prompt,
            result_model=RootCauseLLMOutput,
        )
        return RootCauseResult.from_llm_output(
            complaint_id=complaint.complaint_id,
            llm_output=llm_output,
            model_used=served_by,
        )

    def analyze_batch(
        self,
        complaints: list[ClassifiedComplaint],
        checkpoint_path: Path | None = None,
        failed_path: Path | None = None,
    ) -> list[RootCausedComplaint]:
        """
        Run root cause analysis over a batch of classified complaints.
        Writes a checkpoint CSV every CHECKPOINT_EVERY rows so long runs
        are resumable / auditable, and logs any row that fails after all
        retries to `failed_path` instead of dropping it silently.
        """
        results: list[RootCausedComplaint] = []
        failed: list[dict] = []

        for i, complaint in enumerate(complaints, start=1):
            try:
                rc_result = self.analyze_one(complaint)
                results.append(RootCausedComplaint.from_classified_and_result(complaint, rc_result))
            except (LLMParseError, OpenRouterError, OpenRouterTransientError) as exc:
                logger.error("Root cause analysis failed for complaint_id=%s: %s", complaint.complaint_id, exc)
                failed.append({"complaint_id": complaint.complaint_id, "error": str(exc)})

            if checkpoint_path and i % CHECKPOINT_EVERY == 0:
                self._write_csv(results, checkpoint_path)
                logger.info("Checkpoint: %d/%d complaints processed -> %s", i, len(complaints), checkpoint_path)

        if checkpoint_path:
            self._write_csv(results, checkpoint_path)

        if failed and failed_path:
            failed_path.parent.mkdir(parents=True, exist_ok=True)
            failed_path.write_text(json.dumps(failed, indent=2))
            logger.warning("%d complaint(s) failed root cause analysis. See %s", len(failed), failed_path)

        logger.info("Root cause analysis complete: %d succeeded, %d failed.", len(results), len(failed))
        return results

    @staticmethod
    def _write_csv(rows: list[RootCausedComplaint], path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        df = pd.DataFrame([r.model_dump() for r in rows])
        df.to_csv(path, index=False)


def _load_classified_complaints(csv_path: Path) -> list[ClassifiedComplaint]:
    df = pd.read_csv(csv_path)
    df = df.where(pd.notnull(df), None)
    return [ClassifiedComplaint(**row) for row in df.to_dict(orient="records")]


def main() -> None:
    parser = argparse.ArgumentParser(description="Agent 2 -- Root Cause Analysis")
    parser.add_argument("--input", type=Path, default=Path("outputs/classified_complaints.csv"))
    parser.add_argument("--output", type=Path, default=Path("outputs/root_caused_complaints.csv"))
    parser.add_argument("--failed", type=Path, default=Path("outputs/root_cause_failed.json"))
    parser.add_argument("--model", type=str, default=None, help="Force a MODEL_REGISTRY key, e.g. gemini")
    args = parser.parse_args()

    complaints = _load_classified_complaints(args.input)
    logger.info("Loaded %d classified complaints from %s", len(complaints), args.input)

    agent = RootCauseAgent(model_key=args.model)
    agent.analyze_batch(complaints, checkpoint_path=args.output, failed_path=args.failed)


if __name__ == "__main__":
    main()
