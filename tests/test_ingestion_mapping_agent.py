import pytest
from pydantic import ValidationError

from agents.ingestion_mapping_agent import IngestionMappingAgent
from data.ingestion.schema import STANDARD_COLUMNS, SourceType


def test_mapping_agent_returns_exact_stable_schema():
    result = IngestionMappingAgent().map_external_record(
        {
            "case_no": "42",
            "branch": "Downtown",
            "opened": "2026-08-01",
            "details": "  Checkout queue was too long.  ",
        },
        source=SourceType.SUPPORT_TICKET,
        source_prefix="TICKET",
        column_map={"id": "case_no", "store": "branch", "date": "opened", "text": "details"},
        row_index=1,
    )

    assert list(result.complaint.model_dump().keys()) == STANDARD_COLUMNS
    assert result.complaint.complaint_id == "TICKET-42"
    assert result.complaint.customer_text == "Checkout queue was too long."


def test_mapping_agent_rejects_blank_complaint_text():
    with pytest.raises(ValidationError):
        IngestionMappingAgent().map_external_record(
            {"id": "42", "text": "   "},
            source=SourceType.SUPPORT_TICKET,
            source_prefix="TICKET",
            column_map={"id": "id", "text": "text"},
            row_index=1,
        )

