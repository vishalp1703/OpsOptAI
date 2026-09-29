"""Read-only trend retrieval for the optional chat API.

Still zero LLM-generated SQL and zero free-form query construction — same
safety property as before. The only change is that the question's keywords
now pick between a small, fixed set of known-safe aggregation queries
instead of always returning "recent records" regardless of what was asked.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime
from typing import Any

from dateutil import parser as date_parser
from sqlalchemy import func, select

from data.database import session_scope
from data.models import Complaint, ComplaintClassification, RootCause

# Order matters: checked top-to-bottom, first match wins. Keep "trend" ahead
# of "category" etc. since a question can plausibly mention both.
_INTENT_KEYWORDS = [
    ("trend", ["trend", "over time", "weekly", "monthly", "by month", "by week", "time series"]),
    ("category", ["category", "categories", "type of complaint", "types of complaint", "kinds of complaint"]),
    ("severity", ["severity", "how severe", "critical", "urgent"]),
    ("store", ["store", "location", "branch", "which store"]),
    ("root_cause", ["root cause", "why are", "why is", "underlying cause"]),
]


def _detect_intent(question: str) -> str:
    q = question.lower()
    for intent, keywords in _INTENT_KEYWORDS:
        if any(kw in q for kw in keywords):
            return intent
    return "recent"


def _parse_date(raw: str | None) -> datetime | None:
    """Your `date` column has mixed formats (e.g. "May 18, 2026" and
    "25-07-2026") since it stores each source's own date string as-is.
    dateutil handles most of that; anything it can't parse is skipped and
    counted, not silently dropped without a trace."""
    if not raw:
        return None
    try:
        return date_parser.parse(str(raw).strip())
    except (ValueError, OverflowError, TypeError):
        return None


def _load_all_rows(session) -> list[dict]:
    rows = session.execute(
        select(Complaint.complaint_id, Complaint.store, Complaint.date, Complaint.customer_text,
               ComplaintClassification.category, ComplaintClassification.severity,
               RootCause.root_cause_category)
        .outerjoin(ComplaintClassification, Complaint.complaint_id == ComplaintClassification.complaint_id)
        .outerjoin(RootCause, Complaint.complaint_id == RootCause.complaint_id)
    ).all()
    return [dict(r._mapping) for r in rows]


def _breakdown(rows: list[dict], field: str, label: str, limit: int) -> tuple[str, list[dict]]:
    counter = Counter(r.get(field) or "Unclassified" for r in rows)
    top = counter.most_common(limit)
    total = sum(counter.values())
    parts = ", ".join(f"{name} ({count})" for name, count in top)
    answer = f"Out of {total} complaint(s), breakdown by {label}: {parts}."
    evidence = [{"label": name, "count": count} for name, count in top]
    return answer, evidence


def _trend(rows: list[dict], limit: int) -> tuple[str, list[dict]]:
    monthly: Counter[str] = Counter()
    skipped = 0
    for r in rows:
        dt = _parse_date(r.get("date"))
        if dt is None:
            skipped += 1
            continue
        monthly[dt.strftime("%Y-%m")] += 1

    ordered = sorted(monthly.items())[-limit:]
    parts = ", ".join(f"{month}: {count}" for month, count in ordered)
    note = f" ({skipped} record(s) had unparseable dates and were excluded.)" if skipped else ""
    answer = f"Complaint volume by month: {parts}.{note}" if ordered else "No complaints with parseable dates found."
    evidence = [{"month": month, "count": count} for month, count in ordered]
    return answer, evidence


def _recent(session, question: str, total: int, classified: int, limit: int) -> dict[str, Any]:
    rows = session.execute(
        select(Complaint.complaint_id, Complaint.store, Complaint.date, Complaint.customer_text,
               ComplaintClassification.category, ComplaintClassification.severity,
               RootCause.root_cause_category)
        .outerjoin(ComplaintClassification, Complaint.complaint_id == ComplaintClassification.complaint_id)
        .outerjoin(RootCause, Complaint.complaint_id == RootCause.complaint_id)
        .order_by(Complaint.date.desc()).limit(limit)
    ).all()
    evidence = [dict(row._mapping) for row in rows]
    answer = (f"OpsPilot has {total} stored complaint(s), of which {classified} are classified. "
              f"Showing the {len(evidence)} most recent records.")
    return {"answer": answer, "question": question, "intent": "recent", "evidence": evidence,
            "generated_at": date.today().isoformat()}


def answer_question(question: str, limit: int = 10) -> dict[str, Any]:
    """Return grounded trend/breakdown data without generating SQL from user text.

    Keyword-based intent routing only, still no LLM and no dynamic query
    construction from user text — same safety guarantee as before. Unmatched
    questions fall back to the original "recent records" behavior.
    """
    with session_scope() as session:
        total = session.scalar(select(func.count()).select_from(Complaint)) or 0
        classified = session.scalar(select(func.count()).select_from(ComplaintClassification)) or 0
        intent = _detect_intent(question)

        if intent == "recent":
            return _recent(session, question, total, classified, limit)

        all_rows = _load_all_rows(session)

    handlers = {
        "trend": lambda: _trend(all_rows, limit),
        "category": lambda: _breakdown(all_rows, "category", "category", limit),
        "severity": lambda: _breakdown(all_rows, "severity", "severity", limit),
        "store": lambda: _breakdown(all_rows, "store", "store", limit),
        "root_cause": lambda: _breakdown(all_rows, "root_cause_category", "root cause", limit),
    }
    answer, evidence = handlers[intent]()

    return {
        "answer": answer,
        "question": question,
        "intent": intent,
        "evidence": evidence,
        "generated_at": date.today().isoformat(),
    }