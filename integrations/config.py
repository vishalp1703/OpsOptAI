"""Safe configuration for optional external integration sources.

Credentials are never stored in source configuration. Use environment-variable
names that resolve only inside the ingestion process.
"""
from __future__ import annotations

import json
import os
import re
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class SourceKind(str, Enum):
    FILE = "file"
    DATABASE = "database"


class ExternalSourceConfig(BaseModel):
    """A source definition. target_source must be a configured OpsPilot source."""
    name: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    kind: SourceKind
    target_source: str
    enabled: bool = False
    mapping: dict[str, str] = Field(default_factory=dict)
    file_path: str | None = None
    database_url_env: str | None = None
    table: str | None = None
    cursor_column: str | None = None
    allow_llm_mapping: bool = False
    mapper_model: str = "gpt"

    @field_validator("database_url_env")
    @classmethod
    def validate_env_name(cls, value: str | None) -> str | None:
        if value and not _ENV_NAME.fullmatch(value):
            raise ValueError("database_url_env must be an uppercase environment-variable name")
        return value

    @field_validator("table", "cursor_column")
    @classmethod
    def validate_identifier(cls, value: str | None) -> str | None:
        if value and not _IDENTIFIER.fullmatch(value):
            raise ValueError("table and cursor_column may contain only a SQL identifier")
        return value

    def source_database_url(self) -> str:
        if not self.database_url_env:
            raise ValueError(f"Source '{self.name}' has no database_url_env configured")
        value = os.getenv(self.database_url_env)
        if not value:
            raise ValueError(f"Environment variable '{self.database_url_env}' is not set")
        return value


def load_sources(path: str | Path) -> list[ExternalSourceConfig]:
    """Load source definitions from JSON; disabled sources remain harmless."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    entries: list[dict[str, Any]] = raw.get("sources", raw) if isinstance(raw, dict) else raw
    return [ExternalSourceConfig.model_validate(item) for item in entries]
