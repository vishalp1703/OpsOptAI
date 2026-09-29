"""Rule-based schema mapper with persistent, reviewable mapping memory."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Iterable

LOGICAL_FIELDS = ("id", "store", "date", "text")
ALIASES = {
    "id": ("id", "review_id", "ticket_id", "log_id", "note_id", "feedback_id", "survey_id", "post_id", "incident_id"),
    "store": ("store", "store_id", "store_location", "location", "branch", "account", "store_mentioned"),
    "date": ("date", "created_date", "review_date", "transaction_date", "note_date", "submission_date", "response_date", "post_date", "incident_date"),
    "text": ("text", "description", "review_text", "notes", "note_text", "comments", "feedback_text", "details", "content"),
}


def _normalise(value: str) -> str:
    return "".join(ch for ch in value.lower() if ch.isalnum())


def schema_fingerprint(columns: Iterable[str]) -> str:
    material = "|".join(sorted(_normalise(column) for column in columns))
    return hashlib.sha256(material.encode()).hexdigest()[:20]


def infer_mapping(columns: Iterable[str]) -> tuple[dict[str, str], dict[str, float]]:
    """Map external headers to the four logical landing fields without an LLM."""
    actual = list(columns)
    index = {_normalise(column): column for column in actual}
    mapping: dict[str, str] = {}
    confidence: dict[str, float] = {}
    for field, aliases in ALIASES.items():
        for alias in aliases:
            if _normalise(alias) in index:
                mapping[field] = index[_normalise(alias)]
                confidence[field] = 1.0 if _normalise(alias) == _normalise(field) else 0.9
                break
    return mapping, confidence


class MappingStore:
    """Local mapping memory keyed by source name and stable header signature."""
    def __init__(self, directory: str | Path = "data/schema_mappings"):
        self.directory = Path(directory)

    def load(self, source_name: str, columns: Iterable[str]) -> dict[str, str] | None:
        path = self.directory / f"{source_name}-{schema_fingerprint(columns)}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))["mapping"]

    def save(self, source_name: str, columns: Iterable[str], mapping: dict[str, str], confidence: dict[str, float]) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"{source_name}-{schema_fingerprint(columns)}.json"
        path.write_text(json.dumps({"mapping": mapping, "confidence": confidence}, indent=2), encoding="utf-8")
        return path
