"""
app.py
======
OpsPilot AI — main entry point.

Phase 1 usage (Ingestion Mapping Agent):
    python app.py ingest                      # ingest all sources
    python app.py ingest --sources customer_reviews support_tickets
    python app.py ingest --raw-dir data/raw --cleaned-dir data/cleaned

The Ingestion Mapping Agent is the mandatory first stage: each source parser
maps its fields into the stable StandardComplaint schema and validates every
record before classification or downstream agents can receive it.

Later phases (classification, agent workflows, API server, dashboard
launch) will hang additional subcommands off this same CLI as they're
built, so this stays the single entry point for the whole project.
"""

from __future__ import annotations

import argparse
import logging
import sys

from data.ingestion.pipeline import run_ingestion

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("opspilot")


def cmd_ingest(args: argparse.Namespace) -> int:
    logger.info("Starting Phase 1 ingestion...")
    try:
        summary = run_ingestion(
            raw_dir=args.raw_dir,
            cleaned_dir=args.cleaned_dir,
            sources=args.sources,
        )
    except FileNotFoundError as e:
        logger.error(str(e))
        logger.error("Tip: run `python data/generate_sample_data.py` to create sample raw files first.")
        return 1

    summary.print_report()

    any_errors = any(r.status == "error" for r in summary.results)
    return 1 if any_errors else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="opspilot",
        description="OpsPilot AI — Operational Intelligence Platform",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest_parser = subparsers.add_parser("ingest", help="Run Phase 1: data ingestion & cleaning")
    ingest_parser.add_argument("--raw-dir", default="data/raw", help="Directory of raw source files")
    ingest_parser.add_argument("--cleaned-dir", default="data/cleaned", help="Directory to write cleaned CSVs")
    ingest_parser.add_argument(
        "--sources", nargs="*", default=None,
        help="Optional subset of source keys to ingest (default: all). "
             "Choices: customer_reviews support_tickets pos_logs employee_feedback "
             "surveys social_media crm_notes incident_reports",
    )
    ingest_parser.set_defaults(func=cmd_ingest)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
