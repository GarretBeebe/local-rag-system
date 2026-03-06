"""
OpenAI-compatible chat completions endpoint backed by the local RAG pipeline.

Implements POST /v1/chat/completions so any OpenAI-compatible client
(Open WebUI, Chatbox, etc.) can point at this server and query the
local knowledge base.

The server always uses the model configured in settings.GEN_MODEL regardless
of the model name sent by the client.

Run with:
    uvicorn web.api_server:app --host 0.0.0.0 --port 8000
"""

import logging
import uuid

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from api.query_rag import ask
from settings import GEN_MODEL

logger = logging.getLogger(__name__)

app = FastAPI()


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    model: str
    messages: list[ChatMessage] = Field(min_length=1)


@app.post("/v1/chat/completions")
def chat(req: ChatRequest):
    if req.model != GEN_MODEL:
        logger.warning("Client requested model %r but server is using %r", req.model, GEN_MODEL)

    question = req.messages[-1].content
    if not question.strip():
        raise HTTPException(status_code=400, detail="Last message content is empty.")

    try:
        answer = ask(question)
    except Exception as e:
        logger.exception("RAG pipeline error")
        raise HTTPException(status_code=500, detail=str(e)) from e

    return {
        "id": f"rag-{uuid.uuid4()}",
        "object": "chat.completion",
        "model": GEN_MODEL,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": answer,
                },
                "finish_reason": "stop",
            }
        ],
    }
