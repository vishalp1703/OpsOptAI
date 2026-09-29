"""
OpsPilot AI end-to-end integration runner.

Runs the completed local phases in order:
  1. Ingestion
  2. Optional live LLM classification
  3. Optional live multi-agent analysis
  4. Database initialization and output loading
  5. Dashboard data-layer smoke test
  6. Phase 6 isolated test suite

Usage:
    python run_all.py
    python run_all.py --with-llm --limit 10
    python run_all.py --with-llm --model gpt --limit 10 --launch-dashboard

The default command does not make OpenRouter calls. Add --with-llm only when
OPENROUTER_API_KEY is configured and you approve the associated API usage.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent
PYTHON = sys.executable
load_dotenv(PROJECT_ROOT / ".env")


def run_step(name: str, command: list[str]) -> None:
    """Run one command from the project root and stop immediately on failure."""
    print(f"\n{'=' * 72}\n{name}\n{'=' * 72}")
    print("$", " ".join(command))
    subprocess.run(command, cwd=PROJECT_ROOT, check=True)


def require_file(path: Path, description: str) -> None:
    if not path.exists():
        raise FileNotFoundError(f"{description} was not created: {path}")


def dashboard_smoke_test() -> None:
    """Check that the dashboard's data access and metric functions work."""
    code = """
from dashboard.data_access import introspect, load_complaints_full
from dashboard.metrics import compute_risk_score, confidence_health

schema = introspect()
missing = [name for name, detail in schema.items() if not detail["exists"]]
if missing:
    raise RuntimeError(f"Dashboard database tables are missing: {', '.join(missing)}")

complaints = load_complaints_full()
risk = compute_risk_score(complaints)
confidence = confidence_health(complaints)
print(f"Dashboard smoke test passed: {len(complaints)} complaint row(s), "
      f"risk={risk['score']}, avg_confidence={confidence['mean']}")
"""
    run_step("Phase 5 — dashboard data-layer smoke test", [PYTHON, "-c", code])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run OpsPilot AI's completed phases as one integration check."
    )
    parser.add_argument(
        "--with-llm",
        action="store_true",
        help="Run Phases 2 and 3 using OpenRouter. This makes external API calls.",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Model key or OpenRouter slug for Phases 2 and 3.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum complaints classified in live mode (default: 10).",
    )
    parser.add_argument(
        "--skip-phase6-tests",
        action="store_true",
        help="Skip the isolated pytest suite for realtime ingestion.",
    )
    parser.add_argument(
        "--launch-dashboard",
        action="store_true",
        help="Launch Streamlit after all checks pass (this command stays running).",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()

    if args.limit < 1:
        raise ValueError("--limit must be at least 1")

    raw_dir = PROJECT_ROOT / "data" / "raw"
    cleaned_master = PROJECT_ROOT / "data" / "cleaned" / "master_cleaned.csv"
    classified = PROJECT_ROOT / "outputs" / "classified_complaints.csv"

    if not raw_dir.exists() or not any(raw_dir.glob("*.csv")):
        raise FileNotFoundError(
            "No raw CSV files found in data/raw. Generate sample data first with "
            "python data/generate_sample_data.py or add source files."
        )

    run_step("Phase 1 — ingestion and normalization", [PYTHON, "app.py", "ingest"])
    require_file(cleaned_master, "Phase 1 master cleaned file")

    if args.with_llm:
        if not os.getenv("OPENROUTER_API_KEY"):
            raise RuntimeError(
                "OPENROUTER_API_KEY is not set. Add it to .env before using --with-llm."
            )

        classify_cmd = [
            PYTHON, "-m", "agents.classification_agent",
            "--input", str(cleaned_master),
            "--output", str(classified),
            "--limit", str(args.limit),
        ]
        if args.model:
            classify_cmd.extend(["--model", args.model])
        run_step("Phase 2 — LLM classification", classify_cmd)
        require_file(classified, "Phase 2 classified complaints file")

        pipeline_cmd = [
            PYTHON, "-m", "workflows.pipeline",
            "--input", str(classified), "--force",
        ]
        if args.model:
            pipeline_cmd.extend(["--model", args.model])
        run_step("Phase 3 — multi-agent workflow", pipeline_cmd)
    else:
        print(
            "\nPhases 2 and 3 skipped: rerun with --with-llm to make OpenRouter calls. "
            "Any existing outputs will still be loaded in Phase 4."
        )

    run_step("Phase 4 — initialize database", [PYTHON, "-m", "data.init_db"])
    run_step("Phase 4 — load available pipeline outputs", [PYTHON, "-m", "data.load_outputs"])

    dashboard_smoke_test()

    if not args.skip_phase6_tests:
        if importlib.util.find_spec("pytest") is not None:
            phase6_command = [PYTHON, "-m", "pytest", "tests", "-q"]
        else:
            # tests/test_phase6.py intentionally supports direct execution so
            # the integration runner still works before pytest is installed.
            print("pytest is not installed; using the Phase 6 standalone test runner.")
            phase6_command = [PYTHON, "tests/test_phase6.py"]
        run_step("Project test suite — ingestion, integrations, and realtime", phase6_command)

    print("\n✅ OpsPilot integration run completed successfully.")

    if args.launch_dashboard:
        run_step(
            "Phase 5 — launch Streamlit dashboard",
            [PYTHON, "-m", "streamlit", "run", "dashboard/app.py"],
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
