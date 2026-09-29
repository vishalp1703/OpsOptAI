"""
data.ingestion
===============
Phase 1 — Data Ingestion.

Reads raw complaint data from multiple operational sources (CSV/Excel),
cleans it, and normalizes every record into the StandardComplaint schema
defined in schema.py. This package is designed to be imported by later
phases (agents, workflows) without modification.

Public API:
    from data.ingestion import run_ingestion
    from data.ingestion.schema import StandardComplaint, SourceType
"""

from data.ingestion.schema import StandardComplaint, SourceType  # noqa: F401


def run_ingestion(*args, **kwargs):
    """Lazy export that avoids a schema/parser import cycle at startup."""
    from data.ingestion.pipeline import run_ingestion as _run_ingestion
    return _run_ingestion(*args, **kwargs)
