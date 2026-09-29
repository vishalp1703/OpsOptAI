"""Optional unified source -> existing OpsPilot landing/promotion pipeline."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from agents.ingestion_mapping_agent import IngestionMappingAgent
from data.ingestion.schema import SourceType
from workflows.realtime import config as realtime_config
from workflows.realtime.landing_writer import write_raw_record
from workflows.realtime.promotion import promote_pending

from .adapters import read_records
from .config import ExternalSourceConfig, load_sources
from .mapping import MappingStore, infer_mapping
from .llm_mapper import infer_mapping_with_llm


@dataclass
class SourceRunResult:
    name: str
    landed: int = 0
    promoted: int = 0
    skipped: int = 0
    mapping: dict[str, str] | None = None


TARGET_SOURCE_TYPES = {
    "customer_reviews": (SourceType.CUSTOMER_REVIEW, "REVIEW"),
    "support_tickets": (SourceType.SUPPORT_TICKET, "TICKET"),
    "pos_logs": (SourceType.POS_LOG, "POS"),
    "employee_feedback": (SourceType.EMPLOYEE_FEEDBACK, "EMP"),
    "surveys": (SourceType.SURVEY, "SURVEY"),
    "social_media": (SourceType.SOCIAL_MEDIA, "SOCIAL"),
    "crm_notes": (SourceType.CRM_NOTE, "CRM"),
    "incidents": (SourceType.INCIDENT_REPORT, "INCIDENT"),
}


def _mapping_for(config: ExternalSourceConfig, sample: dict, store: MappingStore) -> dict[str, str]:
    columns = sample.keys()
    remembered = store.load(config.name, columns)
    if remembered:
        return remembered
    inferred, confidence = infer_mapping(columns)
    invalid = {field: column for field, column in config.mapping.items() if column not in columns}
    if invalid:
        details = ", ".join(f"{field} -> {column}" for field, column in invalid.items())
        raise ValueError(f"Source '{config.name}' mapping names columns that do not exist: {details}")
    mapping = {**inferred, **config.mapping}
    missing = {"id", "text"} - set(mapping)
    if missing and config.allow_llm_mapping:
        llm_mapping = infer_mapping_with_llm(columns, config.mapper_model)
        mapping = {**mapping, **llm_mapping}
        missing = {"id", "text"} - set(mapping)
    if missing:
        raise ValueError(f"Source '{config.name}' needs explicit mapping for: {', '.join(sorted(missing))}")
    store.save(config.name, columns, mapping, confidence)
    return mapping


def run_source(config: ExternalSourceConfig, limit: int = 200, mapping_store: MappingStore | None = None) -> SourceRunResult:
    if not config.enabled:
        return SourceRunResult(name=config.name)
    if config.target_source not in realtime_config.SOURCES:
        raise ValueError(f"Unknown target_source '{config.target_source}'")
    source_type, source_prefix = TARGET_SOURCE_TYPES[config.target_source]
    records = iter(read_records(config, limit))
    first = next(records, None)
    if first is None:
        return SourceRunResult(name=config.name)
    mapping = _mapping_for(config, first, mapping_store or MappingStore())
    landed = 0
    skipped = 0
    agent = IngestionMappingAgent()
    for row_index, record in enumerate((first, *records), start=1):
        try:
            mapped = agent.map_external_record(record, source_type, source_prefix, mapping, row_index).complaint
        except ValidationError:
            skipped += 1
            continue
        payload = {
            "id": record.get(mapping["id"]),
            "store": mapped.store,
            "date": mapped.date.isoformat() if mapped.date else None,
            "text": mapped.customer_text,
        }
        if write_raw_record(config.target_source, payload, ingested_via=f"optional:{config.kind.value}") is not None:
            landed += 1
    return SourceRunResult(
        name=config.name,
        landed=landed,
        promoted=promote_pending(config.target_source, limit),
        skipped=skipped,
        mapping=mapping,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run enabled optional integration sources")
    parser.add_argument("--config", type=Path, default=Path("integrations/sources.json"))
    parser.add_argument("--source", default=None, help="Run one configured source by name")
    parser.add_argument("--limit", type=int, default=200)
    args = parser.parse_args()
    failed = False
    for source in load_sources(args.config):
        if args.source and source.name != args.source:
            continue
        try:
            result = run_source(source, args.limit)
            print(
                f"{result.name}: landed={result.landed}, promoted={result.promoted}, "
                f"skipped={result.skipped}, mapping={result.mapping}"
            )
        except (OSError, ValueError, SQLAlchemyError) as exc:
            failed = True
            print(f"{source.name}: FAILED — {exc}")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
