"""External file/database readers. They return records only; no credentials leave this process."""
from __future__ import annotations

from pathlib import Path
from typing import Iterator
from itertools import islice

import pandas as pd
from sqlalchemy import MetaData, Table, create_engine, select

from .config import ExternalSourceConfig, SourceKind


def read_file_records(config: ExternalSourceConfig) -> Iterator[dict]:
    if not config.file_path:
        raise ValueError(f"File source '{config.name}' needs file_path")
    path = Path(config.file_path)
    if not path.is_file():
        raise FileNotFoundError(
            f"File source '{config.name}' cannot find '{path}'. "
            "Set file_path in integrations/sources.json to an existing CSV, XLS, or XLSX file."
        )
    if path.suffix.lower() == ".csv":
        frame = pd.read_csv(path, dtype=str)
    elif path.suffix.lower() in {".xls", ".xlsx"}:
        frame = pd.read_excel(path, dtype=str)
    else:
        raise ValueError("Only CSV, XLS, and XLSX source files are supported")
    yield from frame.where(pd.notnull(frame), None).to_dict(orient="records")


def read_database_records(config: ExternalSourceConfig, limit: int) -> Iterator[dict]:
    if not config.table:
        raise ValueError(f"Database source '{config.name}' needs table")
    engine = create_engine(config.source_database_url(), future=True)
    metadata = MetaData()
    table = Table(config.table, metadata, autoload_with=engine)
    statement = select(table).limit(limit)
    with engine.connect() as connection:
        for row in connection.execute(statement):
            yield dict(row._mapping)


def read_records(config: ExternalSourceConfig, limit: int) -> Iterator[dict]:
    if config.kind is SourceKind.FILE:
        yield from islice(read_file_records(config), limit)
    else:
        yield from read_database_records(config, limit)
