"""
data/database.py

Central SQLAlchemy engine / session / declarative Base for OpsPilot AI.

Mirrors the env-loading pattern already used in agents/config.py (load
.env from project root regardless of where this module is imported from),
so this stays consistent with the rest of the codebase.

DB SWITCHING (dev SQLite -> prod Postgres):
    Controlled entirely by the DATABASE_URL env var. Nothing else in this
    file, models.py, or repository.py needs to change when you move to
    Postgres in prod -- that's the whole point of going through SQLAlchemy
    Core/ORM instead of raw sqlite3.

    Dev (default, no env var needed):
        sqlite:///data/opspilot.db

    Prod (set in .env or real environment):
        DATABASE_URL=postgresql+psycopg2://user:password@host:5432/opspilot

    Note: psycopg2-binary isn't in requirements.txt yet since we're on
    SQLite for now -- add it when Postgres is actually wired up in prod:
        pip install psycopg2-binary
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

# Load .env from project root, same convention as agents/config.py
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

# Default: local SQLite file at data/opspilot.db. Overridable for Postgres.
DEFAULT_SQLITE_PATH = PROJECT_ROOT / "data" / "opspilot.db"
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DEFAULT_SQLITE_PATH}")

# SQLite needs check_same_thread=False to be used across FastAPI's
# request-handling threads / Streamlit's script reruns. This flag is a
# no-op (and invalid) for Postgres, so only apply it conditionally.
_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(
    DATABASE_URL,
    connect_args=_connect_args,
    echo=os.getenv("SQL_ECHO", "false").lower() == "true",  # set SQL_ECHO=true to debug queries
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


class Base(DeclarativeBase):
    """Shared declarative base for every ORM model in data/models.py."""
    pass


def init_db(drop_first: bool = False) -> None:
    """
    Create all tables registered on Base.metadata.

    Must be called AFTER data/models.py has been imported at least once
    (importing models.py is what registers the tables on Base.metadata) --
    see data/init_db.py, which does this correctly as a standalone script.

    Args:
        drop_first: if True, drops every known table before recreating.
            Destructive -- dev/testing use only, never call with True
            against a prod database.
    """
    if drop_first:
        Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def get_db() -> Generator[Session, None, None]:
    """
    FastAPI-style dependency: yields a Session, guarantees it's closed.

    Usage (once app.py wires up FastAPI in a later phase):
        @app.get("/complaints")
        def list_complaints(db: Session = Depends(get_db)):
            ...
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    """
    Plain context-manager version of get_db(), for use outside FastAPI --
    scripts, the Streamlit dashboard, agent code, tests.

    Usage:
        from data.database import session_scope
        with session_scope() as db:
            db.add(some_row)
            # commits automatically on clean exit, rolls back on exception
    """
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
