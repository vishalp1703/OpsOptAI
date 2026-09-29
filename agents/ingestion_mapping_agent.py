"""Ingestion Mapping Agent — the mandatory first agent in the OpsPilot flow.

It is deterministic by design: source parsers or optional external adapters
provide a small mapping to the canonical fields, and this agent validates the
result against StandardComplaint before any LLM classification occurs.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from pydantic import ValidationError

from data.ingestion.schema import SourceType, StandardComplaint
from data.ingestion.utils import clean_str, make_complaint_id, parse_date_safe


@dataclass(frozen=True)
class MappingResult:
    complaint: StandardComplaint
    mapped_fields: tuple[str, ...]


class IngestionMappingAgent:
    """Converts a mapped source record into the stable downstream schema."""

    REQUIRED_FIELDS = ("complaint_id", "source", "store", "date", "customer_text")

    def normalize(self, payload: Mapping, expected_source: SourceType) -> MappingResult:
        data = dict(payload)
        data["source"] = expected_source
        complaint = StandardComplaint(**data)
        return MappingResult(complaint=complaint, mapped_fields=self.REQUIRED_FIELDS)

    def map_external_record(
        self,
        record: Mapping,
        source: SourceType,
        source_prefix: str,
        column_map: Mapping[str, str],
        row_index: int,
    ) -> MappingResult:
        """Map arbitrary headers using id/store/date/text keys and validate them.

        The mapping is explicit or supplied by the schema mapper. This method
        never guesses values beyond the configured mapping and never calls an
        LLM, which makes ingestion reproducible and auditable.
        """
        raw_id = record.get(column_map.get("id", ""))
        payload = {
            "complaint_id": make_complaint_id(source_prefix, raw_id, row_index),
            "source": source,
            "store": clean_str(record.get(column_map.get("store", "")), default="UNKNOWN"),
            "date": parse_date_safe(record.get(column_map.get("date", ""))),
            "customer_text": clean_str(record.get(column_map.get("text", ""))),
        }
        return self.normalize(payload, source)


DEFAULT_INGESTION_MAPPING_AGENT = IngestionMappingAgent()


__all__ = [
    "DEFAULT_INGESTION_MAPPING_AGENT",
    "IngestionMappingAgent",
    "MappingResult",
    "ValidationError",
]

