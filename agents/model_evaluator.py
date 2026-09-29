"""
agents/model_evaluator.py

Benchmarks the candidate models in config.MODEL_REGISTRY against a sample
of real (or synthetic) complaints and recommends the best one for Agent 1,
since we don't have hand-labeled ground truth to compute accuracy against.

Because there's no ground truth, "best" is a composite of things we CAN
measure objectively:

  1. json_validity_rate     -- did the model return parseable JSON at all
  2. schema_compliance_rate -- did the JSON match our Pydantic schema
                                (valid enum values, confidence in [0,1], etc.)
  3. consistency_rate       -- run each complaint twice; how often does the
                                model agree with itself on category+severity?
                                A model that flip-flops on identical input is
                                unreliable regardless of how confident it sounds.
  4. avg_confidence         -- informational; not scored directly (a model
                                that's always "1.0 confident" isn't
                                necessarily better, just more overconfident)
  5. avg_latency_seconds    -- lower is better, matters for batch throughput
  6. est_cost_per_1000      -- lower is better, straightforward $ comparison

Composite score = weighted average of the normalized (0-1) versions of
{compliance, consistency, latency, cost}, weights configurable below.

Usage:

    # sanity-check that your MODEL_REGISTRY slugs are still live on OpenRouter
    python -m agents.model_evaluator --check-slugs

    # run the benchmark against a sample CSV and write a recommendation
    python -m agents.model_evaluator --input data/cleaned/complaints.csv --sample-size 15
"""

from __future__ import annotations

import argparse
import json
import logging
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from agents.classification_agent import ClassificationAgent
from agents.config import BEST_MODEL_FILE, MODEL_REGISTRY, ModelSpec
from agents.openrouter_client import OpenRouterError, list_live_models

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("model_evaluator")

# Weights must sum to 1.0. Tune these to match what you care about most --
# e.g. bump "cost" if you're processing 100k complaints/day, bump
# "consistency" if this feeds automated downstream actions.
SCORE_WEIGHTS = {
    "compliance": 0.35,
    "consistency": 0.30,
    "latency": 0.15,
    "cost": 0.20,
}

OUTPUT_REPORT_JSON = Path("outputs/model_evaluation_report.json")
OUTPUT_REPORT_CSV = Path("outputs/model_evaluation_report.csv")


@dataclass
class ModelBenchmarkResult:
    model_key: str
    spec: ModelSpec
    total_calls: int = 0
    valid_json_count: int = 0
    schema_compliant_count: int = 0
    consistent_count: int = 0          # agreement across repeat runs
    consistency_checks: int = 0
    confidences: list[float] = field(default_factory=list)
    latencies: list[float] = field(default_factory=list)
    prompt_tokens: list[int] = field(default_factory=list)
    completion_tokens: list[int] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def json_validity_rate(self) -> float:
        return self.valid_json_count / self.total_calls if self.total_calls else 0.0

    @property
    def schema_compliance_rate(self) -> float:
        return self.schema_compliant_count / self.total_calls if self.total_calls else 0.0

    @property
    def consistency_rate(self) -> float:
        return self.consistent_count / self.consistency_checks if self.consistency_checks else 0.0

    @property
    def avg_confidence(self) -> float:
        return statistics.mean(self.confidences) if self.confidences else 0.0

    @property
    def avg_latency(self) -> float:
        return statistics.mean(self.latencies) if self.latencies else 0.0

    @property
    def est_cost_per_1000(self) -> float:
        """Estimated USD cost to classify 1000 complaints at observed token usage."""
        if not self.prompt_tokens or not self.completion_tokens:
            return 0.0
        avg_in = statistics.mean(self.prompt_tokens)
        avg_out = statistics.mean(self.completion_tokens)
        cost_per_call = (
            avg_in / 1_000_000 * self.spec.input_cost_per_m
            + avg_out / 1_000_000 * self.spec.output_cost_per_m
        )
        return cost_per_call * 1000


def _check_slugs() -> None:
    logger.info("Fetching live model list from OpenRouter...")
    live = list_live_models()
    for key, spec in MODEL_REGISTRY.items():
        status = "OK" if spec.slug in live else "*** NOT FOUND -- update config.py ***"
        logger.info("  %-10s %-35s %s", key, spec.slug, status)


def run_benchmark(sample_df: pd.DataFrame, repeat: int = 2) -> dict[str, ModelBenchmarkResult]:
    """
    Run every model in MODEL_REGISTRY against every row of sample_df,
    `repeat` times each (to measure self-consistency), and collect metrics.
    """
    results: dict[str, ModelBenchmarkResult] = {
        key: ModelBenchmarkResult(model_key=key, spec=spec)
        for key, spec in MODEL_REGISTRY.items()
    }

    for key, spec in MODEL_REGISTRY.items():
        logger.info("=== Benchmarking %s (%s) ===", key, spec.slug)
        agent = ClassificationAgent(model=key)
        bench = results[key]

        for _, row in sample_df.iterrows():
            text = str(row["customer_text"])
            complaint_id = str(row.get("complaint_id", "sample"))
            run_labels: list[tuple[str, str]] = []  # (category, severity) per repeat

            for r in range(repeat):
                bench.total_calls += 1
                start = time.monotonic()
                try:
                    result = agent.classify_complaint(
                        customer_text=text, complaint_id=f"{complaint_id}_r{r}"
                    )
                    elapsed = time.monotonic() - start
                    bench.valid_json_count += 1
                    bench.schema_compliant_count += 1  # ClassificationResult() already validated it
                    bench.confidences.append(result.confidence)
                    bench.latencies.append(elapsed)
                    run_labels.append((result.category.value, result.severity.value))
                except (OpenRouterError, ValueError) as exc:
                    bench.errors.append(f"{complaint_id}: {exc}")
                    logger.warning("  %s failed on %s: %s", key, complaint_id, exc)

            if len(run_labels) == repeat and repeat > 1:
                bench.consistency_checks += 1
                if len(set(run_labels)) == 1:
                    bench.consistent_count += 1

        logger.info(
            "  -> validity=%.0f%% compliance=%.0f%% consistency=%.0f%% "
            "avg_conf=%.2f avg_latency=%.2fs est_$/1000=%.3f errors=%d",
            bench.json_validity_rate * 100, bench.schema_compliance_rate * 100,
            bench.consistency_rate * 100, bench.avg_confidence, bench.avg_latency,
            bench.est_cost_per_1000, len(bench.errors),
        )

    return results


def _normalize(values: dict[str, float], lower_is_better: bool = False) -> dict[str, float]:
    """Min-max normalize a metric across models to [0, 1] for fair weighting."""
    if not values:
        return {}
    lo, hi = min(values.values()), max(values.values())
    if hi == lo:
        return {k: 1.0 for k in values}
    if lower_is_better:
        return {k: (hi - v) / (hi - lo) for k, v in values.items()}
    return {k: (v - lo) / (hi - lo) for k, v in values.items()}


def score_and_recommend(results: dict[str, ModelBenchmarkResult]) -> tuple[str, pd.DataFrame]:
    """Compute composite scores and return (best_model_key, report_dataframe).

    Models with zero successful calls (dead/deprecated slug, auth failure,
    etc.) are excluded from the normalization pool entirely rather than
    scored. Otherwise their latency and cost both default to 0.0, which
    min-max normalization would read as "best possible" on those two axes
    -- letting a completely broken model earn partial credit it did nothing
    to deserve. They're still included in the final report, pinned to
    composite_score=0.0 and clearly marked, just never eligible to win.
    """
    working = {k: r for k, r in results.items() if r.valid_json_count > 0}
    broken = {k: r for k, r in results.items() if r.valid_json_count == 0}

    compliance = {k: r.schema_compliance_rate for k, r in working.items()}
    consistency = {k: r.consistency_rate for k, r in working.items()}
    latency = {k: r.avg_latency for k, r in working.items()}
    cost = {k: r.est_cost_per_1000 for k, r in working.items()}

    norm_compliance = _normalize(compliance)
    norm_consistency = _normalize(consistency)
    norm_latency = _normalize(latency, lower_is_better=True)
    norm_cost = _normalize(cost, lower_is_better=True)

    rows = []
    for k, r in results.items():
        if k in broken:
            composite = 0.0
        else:
            composite = (
                SCORE_WEIGHTS["compliance"] * norm_compliance.get(k, 0)
                + SCORE_WEIGHTS["consistency"] * norm_consistency.get(k, 0)
                + SCORE_WEIGHTS["latency"] * norm_latency.get(k, 0)
                + SCORE_WEIGHTS["cost"] * norm_cost.get(k, 0)
            )
        rows.append({
            "model_key": k,
            "model_slug": r.spec.slug,
            "total_calls": r.total_calls,
            "json_validity_rate": round(r.json_validity_rate, 3),
            "schema_compliance_rate": round(r.schema_compliance_rate, 3),
            "consistency_rate": round(r.consistency_rate, 3),
            "avg_confidence": round(r.avg_confidence, 3),
            "avg_latency_sec": round(r.avg_latency, 3),
            "est_cost_per_1000_usd": round(r.est_cost_per_1000, 4),
            "error_count": len(r.errors),
            "composite_score": round(composite, 4),
            "status": "FAILED - excluded from ranking" if k in broken else "ok",
        })

    report_df = pd.DataFrame(rows).sort_values("composite_score", ascending=False).reset_index(drop=True)

    if not working:
        raise RuntimeError(
            "Every candidate model failed (0 successful calls each). Check your "
            "OPENROUTER_API_KEY and run --check-slugs before benchmarking again."
        )
    best_key = report_df.iloc[0]["model_key"]
    return best_key, report_df


def save_report(report_df: pd.DataFrame, best_key: str) -> None:
    OUTPUT_REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
    report_df.to_csv(OUTPUT_REPORT_CSV, index=False)

    best_row = report_df.iloc[0].to_dict()
    BEST_MODEL_FILE.write_text(json.dumps({
        "model_key": best_key,
        "model_slug": MODEL_REGISTRY[best_key].slug,
        "composite_score": best_row["composite_score"],
        "evaluated_at": pd.Timestamp.now("UTC").isoformat(),
        "full_report": report_df.to_dict(orient="records"),
    }, indent=2))
    logger.info("Wrote full report -> %s", OUTPUT_REPORT_CSV)
    logger.info("Wrote best-model pointer -> %s (agents will auto-use '%s')", BEST_MODEL_FILE, best_key)


def _cli():
    parser = argparse.ArgumentParser(description="Benchmark candidate OpenRouter models for Agent 1.")
    parser.add_argument("--input", help="Path to cleaned CSV to sample from")
    parser.add_argument("--sample-size", type=int, default=12, help="Number of complaints to sample for benchmarking")
    parser.add_argument("--repeat", type=int, default=2, help="Repeat each complaint N times per model to measure consistency")
    parser.add_argument("--check-slugs", action="store_true", help="Just validate MODEL_REGISTRY slugs against OpenRouter and exit")
    args = parser.parse_args()

    if args.check_slugs:
        _check_slugs()
        return

    if not args.input:
        parser.error("--input is required unless using --check-slugs")

    df = pd.read_csv(args.input)
    sample = df.sample(n=min(args.sample_size, len(df)), random_state=42).reset_index(drop=True)
    logger.info("Benchmarking %d models against %d sampled complaints (x%d repeats each = %d calls/model)",
                len(MODEL_REGISTRY), len(sample), args.repeat, len(sample) * args.repeat)

    results = run_benchmark(sample, repeat=args.repeat)
    best_key, report_df = score_and_recommend(results)
    save_report(report_df, best_key)

    print("\n" + report_df.to_string(index=False))
    print(f"\nRecommended model: {best_key} ({MODEL_REGISTRY[best_key].slug})")
    print("This is now the default for ClassificationAgent() with no --model argument.")


if __name__ == "__main__":
    _cli()
