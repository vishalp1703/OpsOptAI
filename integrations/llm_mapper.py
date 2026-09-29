"""Opt-in LLM fallback for unfamiliar schemas; credentials and row values are never sent."""
from __future__ import annotations

from typing import Iterable

from pydantic import BaseModel, Field

from agents.config import MODEL_REGISTRY
from agents.llm_json import call_and_parse


class MappingSuggestion(BaseModel):
    mapping: dict[str, str] = Field(default_factory=dict)


def infer_mapping_with_llm(columns: Iterable[str], model_key: str = "gpt") -> dict[str, str]:
    """Return logical-field mappings from column names only, never credentials or data values."""
    model = MODEL_REGISTRY.get(model_key)
    if model is None:
        raise ValueError(f"Unknown mapper model '{model_key}'")
    available = list(columns)
    result, _ = call_and_parse(
        model.slug,
        "Map business-data column names to OpsPilot fields. Return JSON only. Never invent columns.",
        "Available columns: " + repr(available) +
        "\nReturn {mapping: {id: column, store: column, date: column, text: column}}."
        " Only include fields you can map confidently.",
        MappingSuggestion,
        temperature=0.0,
        max_tokens=160,
    )
    allowed = set(available)
    return {field: column for field, column in result.mapping.items()
            if field in {"id", "store", "date", "text"} and column in allowed}
