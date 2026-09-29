"""
agents/compare_language_effect.py

Runs the existing Phase 2 benchmark harness (model_evaluator.run_benchmark /
score_and_recommend -- unmodified, imported directly) against two datasets:
the original English fixture and a new Chinese-company fixture, using
identical methodology (same weights, same repeat count), then diffs the
composite scores per model.

This isolates ONE variable: does a model's relative ranking change when the
input is native-language Chinese business complaints vs the English
baseline? Everything else (schema, scoring, weights) stays constant, so the
diff is a fair read on the "DeepSeek does better on Chinese data" hypothesis.

Does NOT touch outputs/best_model.json -- that pointer stays whatever your
last production run set it to. This writes its own separate report files
so you can inspect both runs and the diff without disturbing production.

Usage:
    python -m agents.compare_language_effect \
        --input-en data/cleaned/sample_complaints.csv \
        --input-cn data/cleaned/sample_complaints_cn.csv \
        --sample-size 12 --repeat 2
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import pandas as pd

from agents.model_evaluator import run_benchmark, score_and_recommend

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("compare_language_effect")

OUTPUT_DIR = Path("outputs")

# Columns to surface in the final diff table (base metric name -> compared as _en/_cn)
DIFF_METRICS = [
    "composite_score",
    "schema_compliance_rate",
    "consistency_rate",
    "avg_latency_sec",
    "est_cost_per_1000_usd",
]


def _run_one(label: str, path: str, sample_size: int, repeat: int) -> pd.DataFrame:
    df = pd.read_csv(path)
    sample = df.sample(n=min(sample_size, len(df)), random_state=42).reset_index(drop=True)
    logger.info("=== Dataset: %s (%d rows sampled from %s) ===", label, len(sample), path)

    results = run_benchmark(sample, repeat=repeat)
    _, report_df = score_and_recommend(results)

    out_path = OUTPUT_DIR / f"model_evaluation_report_{label}.csv"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    report_df.to_csv(out_path, index=False)
    logger.info("Wrote %s", out_path)
    return report_df


def diff_reports(en_df: pd.DataFrame, cn_df: pd.DataFrame) -> pd.DataFrame:
    merged = en_df.merge(cn_df, on="model_key", suffixes=("_en", "_cn"), how="outer")

    for metric in DIFF_METRICS:
        merged[f"{metric}_delta"] = merged[f"{metric}_cn"] - merged[f"{metric}_en"]

    cols = ["model_key", "model_slug_en"]
    for metric in DIFF_METRICS:
        cols += [f"{metric}_en", f"{metric}_cn", f"{metric}_delta"]

    return (
        merged[cols]
        .sort_values("composite_score_delta", ascending=False)
        .reset_index(drop=True)
    )


def _cli():
    parser = argparse.ArgumentParser(
        description="Compare model benchmark results across an English vs a Chinese-company dataset."
    )
    parser.add_argument("--input-en", required=True, help="Path to the original English fixture CSV")
    parser.add_argument("--input-cn", required=True, help="Path to the Chinese-company fixture CSV")
    parser.add_argument("--sample-size", type=int, default=12)
    parser.add_argument("--repeat", type=int, default=2)
    args = parser.parse_args()

    en_df = _run_one("en", args.input_en, args.sample_size, args.repeat)
    cn_df = _run_one("cn", args.input_cn, args.sample_size, args.repeat)

    diff_df = diff_reports(en_df, cn_df)
    diff_path = OUTPUT_DIR / "model_evaluation_language_diff.csv"
    diff_df.to_csv(diff_path, index=False)

    print("\n" + diff_df.to_string(index=False))
    print(f"\nWrote diff report -> {diff_path}")

    top_mover = diff_df.sort_values("composite_score_delta", ascending=False).iloc[0]
    print(
        f"\nBiggest positive mover on Chinese data: {top_mover['model_key']} "
        f"(composite {top_mover['composite_score_en']:.3f} -> {top_mover['composite_score_cn']:.3f}, "
        f"delta {top_mover['composite_score_delta']:+.3f})"
    )

    deepseek_row = diff_df[diff_df["model_key"].str.contains("deepseek", case=False, na=False)]
    gemini_row = diff_df[diff_df["model_key"].str.contains("gemini", case=False, na=False)]
    if not deepseek_row.empty and not gemini_row.empty:
        ds, gm = deepseek_row.iloc[0], gemini_row.iloc[0]
        gap_en = gm["composite_score_en"] - ds["composite_score_en"]
        gap_cn = gm["composite_score_cn"] - ds["composite_score_cn"]
        print(
            f"\nGemini-DeepSeek composite gap: {gap_en:+.3f} on English -> {gap_cn:+.3f} on Chinese "
            f"({'gap narrowed' if abs(gap_cn) < abs(gap_en) else 'gap widened'}"
            f"{', DeepSeek overtook Gemini' if gap_cn < 0 <= gap_en else ''})"
        )


if __name__ == "__main__":
    _cli()