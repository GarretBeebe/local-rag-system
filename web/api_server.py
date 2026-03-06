"""
OpenAI-compatible chat completions endpoint backed by the local RAG pipeline.

Implements POST /v1/chat/completions so OpenAI-compatible clients
(Chatbox, Open WebUI, LangChain, etc.) can query the local knowledge base.

The server always uses settings.GEN_MODEL regardless of the model name
sent by the client.

Run with:

    uvicorn web.api_server:app --host 0.0.0.0 --port 8000
"""

import asyncio
import logging
import time
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from api.query_rag import ask
from settings import GEN_MODEL

logger = logging.getLogger(__name__)

app = FastAPI(title="Local RAG API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    model: str
    messages: list[ChatMessage] = Field(min_length=1)


@app.get("/v1/models")
def models():
    return {
        "object": "list",
        "data": [
            {
                "id": GEN_MODEL,
                "object": "model",
                "created": int(time.time()),
                "owned_by": "local",
            }
        ],
    }


@app.get("/models")
def models_alias():
    return models()


@app.post("/v1/chat/completions")
async def chat(req: ChatRequest):
    if req.model != GEN_MODEL:
        logger.warning(
            "Client requested model %r but server uses %r",
            req.model,
            GEN_MODEL,
        )

    last_message = req.messages[-1].content

    if not last_message.strip():
        raise HTTPException(status_code=400, detail="Last message content is empty")

    try:
        answer = await asyncio.to_thread(ask, last_message)
    except Exception as e:
        logger.exception("RAG pipeline error")
        raise HTTPException(status_code=500, detail=str(e)) from e

    return {
        "id": f"rag-{uuid.uuid4()}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": GEN_MODEL,
        "system_fingerprint": "local-rag",
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
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
    }

@app.post("/chat/completions")
async def chat_alias(req: ChatRequest):
    return await chat(req)