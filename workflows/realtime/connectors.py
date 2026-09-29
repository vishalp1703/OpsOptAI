"""
Mock polling connectors.

Each connector mimics the shape of a real vendor API: `fetch_new(cursor)`
returns (records, next_cursor). Swapping a mock for a real integration later
means writing a new class with the same interface and pointing the scheduler
at it — nothing else in Phase 6 changes.

These generate synthetic-but-plausible events each time they're polled, so
you can exercise the whole pipeline (poll -> land -> promote -> classify)
without any real credentials.
"""

from __future__ import annotations

import random
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Optional

STORES = ["Downtown", "Westfield", "Store 12", "Airport", "Riverside"]


class BaseConnector(ABC):
    source: str

    def __init__(self, seed: Optional[int] = None):
        self._rng = random.Random(seed)
        self._cursor = 0

    @abstractmethod
    def _generate_batch(self) -> list[dict]:
        ...

    def fetch_new(self, cursor: Optional[int] = None) -> tuple[list[dict], int]:
        """Real APIs: cursor = last-seen ID/timestamp. Mock: monotonically increasing counter."""
        if cursor is not None:
            self._cursor = cursor
        batch = self._generate_batch()
        self._cursor += len(batch)
        return batch, self._cursor


class POSConnector(BaseConnector):
    source = "pos_logs"

    def _generate_batch(self) -> list[dict]:
        n = self._rng.randint(0, 4)
        out = []
        for _ in range(n):
            out.append({
                "transaction_id": f"POS-{self._rng.randint(100000, 999999)}",
                "store": self._rng.choice(STORES),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "issue_flag": self._rng.choice([None, None, None, "refund", "void", "price_override"]),
                "note": self._rng.choice([
                    "", "", "Customer disputed charge", "Register error, manual override",
                    "Long wait, POS system froze",
                ]),
            })
        return out


class CRMConnector(BaseConnector):
    source = "crm_notes"

    def _generate_batch(self) -> list[dict]:
        n = self._rng.randint(0, 3)
        templates = [
            "Customer called re: billing discrepancy on last visit.",
            "Follow-up requested — unresolved complaint from last week.",
            "Customer praised staff at {store}, wants to be added to loyalty program.",
            "Complaint escalated: repeated order mistakes at {store}.",
        ]
        out = []
        for _ in range(n):
            store = self._rng.choice(STORES)
            out.append({
                "note_id": f"CRM-{self._rng.randint(10000, 99999)}",
                "store": store,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "text": self._rng.choice(templates).format(store=store),
                "agent": self._rng.choice(["A. Patel", "J. Kim", "M. Ortiz"]),
            })
        return out


class EmployeeFeedbackConnector(BaseConnector):
    source = "employee_feedback"

    def _generate_batch(self) -> list[dict]:
        n = self._rng.randint(0, 2)
        templates = [
            "Understaffed on weekend shifts at {store}, customers waiting too long.",
            "Kitchen equipment at {store} keeps breaking down, slows service.",
            "Training on new POS system was rushed, causing errors.",
        ]
        out = []
        for _ in range(n):
            store = self._rng.choice(STORES)
            out.append({
                "feedback_id": f"EMP-{self._rng.randint(1000, 9999)}",
                "store": store,
                "submitted_at": datetime.now(timezone.utc).isoformat(),
                "text": self._rng.choice(templates).format(store=store),
                "anonymous": True,
            })
        return out


class SurveyConnector(BaseConnector):
    source = "surveys"

    def _generate_batch(self) -> list[dict]:
        n = self._rng.randint(0, 3)
        out = []
        for _ in range(n):
            store = self._rng.choice(STORES)
            out.append({
                "response_id": f"SVY-{self._rng.randint(10000, 99999)}",
                "store": store,
                "submitted_at": datetime.now(timezone.utc).isoformat(),
                "rating": self._rng.randint(1, 5),
                "comment": self._rng.choice([
                    "Food was cold when it arrived.",
                    "Great service, will come back!",
                    "Order was wrong twice in a row.",
                    "Store was dirty, tables not cleared.",
                ]),
            })
        return out


POLLING_CONNECTORS: dict[str, type[BaseConnector]] = {
    "pos_logs": POSConnector,
    "crm_notes": CRMConnector,
    "employee_feedback": EmployeeFeedbackConnector,
    "surveys": SurveyConnector,
}
