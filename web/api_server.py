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
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from api.query_rag import ask
from settings import GEN_MODEL

logger = logging.getLogger(__name__)

_SERVER_START = int(time.time())

app = FastAPI(title="Local RAG API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def log_requests(request, call_next):
    logger.info("%s %s", request.method, request.url.path)
    return await call_next(request)


class ChatMessage(BaseModel):
    role: str
    content: Any


class ChatRequest(BaseModel):
    model: str
    messages: list[ChatMessage] = Field(min_length=1)
    stream: bool | None = False


@app.get("/v1/models")
def models():
    return {
        "object": "list",
        "data": [
            {
                "id": GEN_MODEL,
                "object": "model",
                "created": _SERVER_START,
                "owned_by": "local",
            }
        ],
    }


@app.get("/models")
def models_alias():
    return models()


@app.post("/v1/chat/completions")
async def chat(req: ChatRequest):

    if req.stream:
        logger.debug("Streaming requested but not implemented.")

    if req.model != GEN_MODEL:
        logger.warning(
            "Client requested model %r but server uses %r",
            req.model,
            GEN_MODEL,
        )

    content = req.messages[-1].content

    if isinstance(content, str):
        question = content
    elif isinstance(content, list):
        # handle OpenAI structured messages
        question = " ".join(
            item.get("text", "")
            for item in content
            if isinstance(item, dict)
        )
    else:
        raise HTTPException(status_code=400, detail="Unsupported message format")

    question = question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Last message content is empty")

    try:
        answer = await asyncio.wait_for(
            asyncio.to_thread(ask, question),
            timeout=120
        )
    except Exception as e:
        logger.exception("RAG pipeline error")
        raise HTTPException(status_code=500, detail=str(e)) from e

    answer = str(answer or "").strip()
    logger.info("Answer: %s", answer[:200])

    return {
        "id": f"chatcmpl-{uuid.uuid4()}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": GEN_MODEL,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": answer or "No response generated.",
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

@app.get("/")
def root():
    return {"status": "rag-api running"}