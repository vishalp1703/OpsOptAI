"""
Fires realistic mock webhook traffic at a running webhook_server, so you can
watch the full pipeline (webhook -> landing -> promotion -> classification
-> dashboard) work end to end without any real vendor accounts.

Usage:
    # In one terminal:
    uvicorn workflows.realtime.webhook_server:app --port 8001

    # In another:
    python -m workflows.realtime.mock_simulator --count 10 --delay 2

Polling sources don't need a simulator — they generate their own mock data
every time scheduler.py polls them (see connectors.py).
"""

from __future__ import annotations

import argparse
import random
import time
import uuid

import requests

from . import config

BASE_URL = "http://127.0.0.1:8001"
STORES = ["Downtown", "Westfield", "Store 12", "Airport", "Riverside"]

REVIEW_TEXTS = [
    "Waited 40 minutes for a simple order, unacceptable.",
    "Best experience I've had in months, staff were fantastic.",
    "Food was cold and the order was wrong.",
]
TICKET_TEXTS = [
    "Refund never processed after 2 weeks, please help.",
    "App keeps crashing when I try to check in a reward.",
]
SOCIAL_TEXTS = [
    "Just had the worst experience at your Downtown location, never again.",
    "Loved the new menu items, highly recommend!",
]
INCIDENT_TEXTS = [
    "Customer slipped near the entrance, minor injury reported.",
    "Freezer malfunction overnight, inventory loss under review.",
]

GENERATORS = {
    "customer_reviews": REVIEW_TEXTS,
    "support_tickets": TICKET_TEXTS,
    "social_media": SOCIAL_TEXTS,
    "incidents": INCIDENT_TEXTS,
}


def fire_one(source: str):
    path = config.SOURCES[source]["webhook_path"]
    payload = {
        "id": str(uuid.uuid4()),
        "store": random.choice(STORES),
        "text": random.choice(GENERATORS[source]),
        "timestamp": time.time(),
    }
    headers = {config.WEBHOOK_HEADER_NAME: config.WEBHOOK_SHARED_SECRET}
    resp = requests.post(f"{BASE_URL}{path}", json=payload, headers=headers, timeout=5)
    print(f"[{source}] {resp.status_code} {resp.json()}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--delay", type=float, default=1.0)
    args = parser.parse_args()

    sources = list(GENERATORS.keys())
    for _ in range(args.count):
        fire_one(random.choice(sources))
        time.sleep(args.delay)


if __name__ == "__main__":
    main()
