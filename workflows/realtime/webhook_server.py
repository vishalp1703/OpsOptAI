"""
Webhook receiver for the 4 push-based sources: customer_reviews,
support_tickets, social_media, incidents.

Run standalone for testing:
    uvicorn workflows.realtime.webhook_server:app --reload --port 8001

In production this is what you'd point a real vendor's webhook config at
(Zendesk, a review-aggregator, a social-listening tool, an incident-mgmt
system) — swap the shared-secret check for that vendor's real signature
verification when you're ready to go live with credentials.

Every payload is landed as-is (raw), then immediate keyword-based alert
rules run synchronously (fast, no LLM call) before responding. Full
classification happens later on the scheduled batch job — this endpoint's
job is just "get it in the door safely and fast."
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Header, HTTPException, Request

from . import config
from .alerting import check_immediate_keywords
from .landing_writer import LandingWriteError, write_raw_record

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("opspilot.realtime.webhook_server")

app = FastAPI(title="OpsPilot Realtime Webhooks")

WEBHOOK_SOURCES = {
    cfg["webhook_path"]: source
    for source, cfg in config.SOURCES.items()
    if cfg["mode"].value == "webhook"
}


def _verify_secret(secret_header: str | None):
    if secret_header != config.WEBHOOK_SHARED_SECRET:
        raise HTTPException(status_code=401, detail="Invalid or missing webhook secret.")


async def _handle_webhook(source: str, request: Request, secret_header: str | None):
    _verify_secret(secret_header)
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Body must be valid JSON.")

    try:
        row_id = write_raw_record(source=source, payload=payload, ingested_via="webhook")
    except LandingWriteError as e:
        raise HTTPException(status_code=500, detail=str(e))

    if row_id is None:
        # Landing table missing — don't tell the vendor it's their fault.
        raise HTTPException(status_code=503, detail=f"'{source}' ingestion temporarily unavailable.")

    alert = check_immediate_keywords(source, payload)

    return {"status": "accepted", "landing_row_id": row_id, "alert_fired": alert is not None}


@app.post("/webhooks/customer_reviews")
async def webhook_customer_reviews(request: Request, x_opspilot_webhook_secret: str | None = Header(default=None)):
    return await _handle_webhook("customer_reviews", request, x_opspilot_webhook_secret)


@app.post("/webhooks/support_tickets")
async def webhook_support_tickets(request: Request, x_opspilot_webhook_secret: str | None = Header(default=None)):
    return await _handle_webhook("support_tickets", request, x_opspilot_webhook_secret)


@app.post("/webhooks/social_media")
async def webhook_social_media(request: Request, x_opspilot_webhook_secret: str | None = Header(default=None)):
    return await _handle_webhook("social_media", request, x_opspilot_webhook_secret)


@app.post("/webhooks/incidents")
async def webhook_incidents(request: Request, x_opspilot_webhook_secret: str | None = Header(default=None)):
    return await _handle_webhook("incidents", request, x_opspilot_webhook_secret)


@app.get("/healthz")
async def healthz():
    return {"status": "ok", "webhook_sources": list(WEBHOOK_SOURCES.values())}
