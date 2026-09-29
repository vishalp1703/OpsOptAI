"""Optional read-only chat retrieval API. Run with uvicorn integrations.chat_api:app."""
from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi import Header, HTTPException
from pydantic import BaseModel, Field

from .retrieval import answer_question

app = FastAPI(title="OpsPilot Retrieval API", version="0.1.0")


class ChatRequest(BaseModel):
    question: str = Field(min_length=3, max_length=1000)
    limit: int = Field(default=10, ge=1, le=50)


def _verify_token(token: str | None) -> None:
    expected = os.getenv("OPSPILOT_CHAT_API_TOKEN")
    if not expected:
        raise HTTPException(status_code=503, detail="Chat API token is not configured.")
    if token != expected:
        raise HTTPException(status_code=401, detail="Invalid chat API token.")


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "mode": "read-only retrieval"}


@app.post("/chat")
def chat(request: ChatRequest, x_opspilot_chat_token: str | None = Header(default=None)) -> dict:
    _verify_token(x_opspilot_chat_token)
    return answer_question(request.question, request.limit)
