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
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from api.query_rag import ask
from api.retrieval import embed_query, rerank
from settings import GEN_MODEL, OLLAMA_BASE_URL

logger = logging.getLogger(__name__)

_SERVER_START = int(time.time())

@asynccontextmanager
async def lifespan(app: FastAPI):
    asyncio.create_task(_warm_models())
    yield


app = FastAPI(title="Local RAG API", lifespan=lifespan)

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

    response = {
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

    if req.stream:
        async def stream():
            for w in answer.split(" "):
                chunk = {
                    "id": response["id"],
                    "object": "chat.completion.chunk",
                    "created": response["created"],
                    "model": GEN_MODEL,
                    "choices": [{"index": 0, "delta": {"content": w + " "}, "finish_reason": None}],
                }
                yield f"data: {json.dumps(chunk)}\n\n"
                await asyncio.sleep(0)

            done_chunk = {
                "id": response["id"],
                "object": "chat.completion.chunk",
                "created": response["created"],
                "model": GEN_MODEL,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            }
            yield f"data: {json.dumps(done_chunk)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(stream(), media_type="text/event-stream")

    return response


@app.post("/chat/completions")
async def chat_alias(req: ChatRequest):
    return await chat(req)

@app.get("/")
def root():
    return {"status": "rag-api running"}


async def _warm_models():
    logger.info("Warming RAG models...")

    async def warm_llm():
        try:
            await asyncio.to_thread(
                requests.post,
                f"{OLLAMA_BASE_URL}/api/generate",
                json={"model": GEN_MODEL, "prompt": "warmup", "stream": False},
                timeout=60,
            )
            logger.info("LLM warmed")
        except Exception as e:
            logger.warning("LLM warmup failed: %s", e)

    async def warm_embed():
        try:
            await asyncio.to_thread(embed_query, "warmup")
            logger.info("Embedding model warmed")
        except Exception as e:
            logger.warning("Embedding warmup failed: %s", e)

    async def warm_reranker():
        try:
            await asyncio.to_thread(rerank, "warmup", ["warmup text"])
            logger.info("Reranker warmed")
        except Exception as e:
            logger.warning("Reranker warmup failed: %s", e)

    await asyncio.gather(warm_llm(), warm_embed(), warm_reranker())

    logger.info("All models warmed")