"""
pipeline.py
===========
Orchestrates Phase 1: discovers raw source files in data/raw/, reads
each (CSV or Excel), routes it to the matching parser in
source_parsers.py, and writes normalized output to data/cleaned/:

    data/cleaned/<source>_cleaned.csv   -- one per source
    data/cleaned/master_cleaned.csv     -- all sources combined

This is the function later phases (and app.py) call:

    from data.ingestion.pipeline import run_ingestion
    summary = run_ingestion()
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from data.ingestion.schema import STANDARD_COLUMNS
from data.ingestion.source_parsers import SOURCE_PARSERS

logger = logging.getLogger("opspilot.ingestion")

# Raw files are matched to a parser by filename stem, e.g.
# "customer_reviews.csv" or "customer_reviews_march.xlsx" both map to
# the "customer_reviews" parser (matched via startswith on the stem).
SUPPORTED_RAW_EXTENSIONS = (".csv", ".xlsx", ".xls")


@dataclass
class SourceIngestResult:
    source_key: str
    file_path: Optional[Path]
    raw_row_count: int = 0
    clean_row_count: int = 0
    status: str = "not_found"  # "ok" | "empty" | "error" | "not_found"
    error: Optional[str] = None


@dataclass
class IngestionSummary:
    results: List[SourceIngestResult] = field(default_factory=list)
    master_row_count: int = 0
    master_path: Optional[Path] = None

    def print_report(self) -> None:
        print("\n" + "=" * 60)
        print("OpsPilot AI — Phase 1 Ingestion Summary")
        print("=" * 60)
        for r in self.results:
            status_icon = {"ok": "✅", "empty": "⚠️ ", "error": "❌", "not_found": "⏭️ "}[r.status]
            print(f"{status_icon} {r.source_key:<20} raw={r.raw_row_count:<6} clean={r.clean_row_count:<6} {r.error or ''}")
        print("-" * 60)
        print(f"TOTAL cleaned records: {self.master_row_count}")
        if self.master_path:
            print(f"Master file: {self.master_path}")
        print("=" * 60 + "\n")


def _read_raw_file(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path, dtype=str, keep_default_na=True)
    return pd.read_excel(path, dtype=str)


def _find_raw_file(raw_dir: Path, source_key: str) -> Optional[Path]:
    """Find a raw file whose stem starts with the source key, any supported extension."""
    candidates = sorted(
        p for p in raw_dir.iterdir()
        if p.is_file()
        and p.suffix.lower() in SUPPORTED_RAW_EXTENSIONS
        and p.stem.lower().startswith(source_key)
    )
    return candidates[0] if candidates else None


def run_ingestion(
    raw_dir: str | Path = "data/raw",
    cleaned_dir: str | Path = "data/cleaned",
    sources: Optional[List[str]] = None,
) -> IngestionSummary:
    """
    Run Phase 1 ingestion end-to-end.

    Args:
        raw_dir: directory containing raw source files.
        cleaned_dir: directory to write cleaned CSVs into.
        sources: optional subset of source keys to process
                 (defaults to all keys in SOURCE_PARSERS).

    Returns:
        IngestionSummary with per-source and master results.
    """
    raw_dir = Path(raw_dir)
    cleaned_dir = Path(cleaned_dir)
    cleaned_dir.mkdir(parents=True, exist_ok=True)

    if not raw_dir.exists():
        raise FileNotFoundError(f"Raw data directory not found: {raw_dir}")

    source_keys = sources or list(SOURCE_PARSERS.keys())
    summary = IngestionSummary()
    all_clean_frames: List[pd.DataFrame] = []

    for source_key in source_keys:
        parser = SOURCE_PARSERS.get(source_key)
        if parser is None:
            logger.warning("No parser registered for source '%s' — skipping", source_key)
            continue

        file_path = _find_raw_file(raw_dir, source_key)
        result = SourceIngestResult(source_key=source_key, file_path=file_path)

        if file_path is None:
            logger.info("No raw file found for '%s' in %s — skipping", source_key, raw_dir)
            summary.results.append(result)
            continue

        try:
            raw_df = _read_raw_file(file_path)
            result.raw_row_count = len(raw_df)

            if raw_df.empty:
                result.status = "empty"
                summary.results.append(result)
                continue

            clean_df = parser(raw_df)
            result.clean_row_count = len(clean_df)
            result.status = "ok" if len(clean_df) > 0 else "empty"

            # Write per-source cleaned CSV
            out_path = cleaned_dir / f"{source_key}_cleaned.csv"
            clean_df.to_csv(out_path, index=False)
            logger.info("Wrote %d records -> %s", len(clean_df), out_path)

            if not clean_df.empty:
                all_clean_frames.append(clean_df)

        except Exception as e:
            result.status = "error"
            result.error = str(e)
            logger.exception("Failed to ingest source '%s' (%s)", source_key, file_path)

        summary.results.append(result)

    # Combine everything into one master file for downstream phases.
    if all_clean_frames:
        master_df = pd.concat(all_clean_frames, ignore_index=True)
        master_df = master_df.drop_duplicates(subset="complaint_id", keep="first")
        master_path = cleaned_dir / "master_cleaned.csv"
        master_df.to_csv(master_path, index=False)
        summary.master_row_count = len(master_df)
        summary.master_path = master_path
    else:
        # Still write an empty master file with correct headers so downstream
        # code (Phase 2+) can always assume the file exists.
        master_path = cleaned_dir / "master_cleaned.csv"
        pd.DataFrame(columns=STANDARD_COLUMNS).to_csv(master_path, index=False)
        summary.master_path = master_path

    return summary
