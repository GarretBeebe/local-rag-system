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
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, suppress
from typing import Any

import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from api.embed import embed
from api.query_rag import ask
from api.retrieval import rerank
from settings import GEN_MODEL, OLLAMA_BASE_URL

logger = logging.getLogger(__name__)

_SERVER_START = int(time.time())
_RAG_EXECUTOR = ThreadPoolExecutor(max_workers=4)
_RAG_CONCURRENCY = asyncio.Semaphore(4)

@asynccontextmanager
async def lifespan(app: FastAPI):
    warm_task = asyncio.create_task(_warm_models())
    try:
        yield
    finally:
        warm_task.cancel()
        with suppress(asyncio.CancelledError):
            await warm_task


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


def _extract_question_from_messages(messages: list[ChatMessage]) -> str:
    """Extract the user question from the last chat message, handling OpenAI formats."""
    content = messages[-1].content

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
    return question


async def _run_rag_with_timeout(question: str, timeout: float = 120.0) -> str:
    """Execute the RAG pipeline with a timeout and bounded concurrency.

    Note: asyncio.wait_for does not cancel the underlying thread, so we also
    bound overall concurrency via a shared ThreadPoolExecutor and semaphore
    to avoid unbounded resource growth under repeated timeouts.
    """
    loop = asyncio.get_running_loop()
    async with _RAG_CONCURRENCY:
        try:
            answer = await asyncio.wait_for(
                loop.run_in_executor(_RAG_EXECUTOR, ask, question),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            logger.warning("RAG pipeline timed out after %.1fs", timeout)
            raise HTTPException(
                status_code=504,
                detail="RAG pipeline timed out while generating an answer.",
            ) from None
        except Exception as e:
            logger.exception("RAG pipeline error")
            raise HTTPException(status_code=500, detail="RAG pipeline error") from e

    return str(answer or "").strip()


def _build_chat_response(answer: str) -> dict[str, Any]:
    """Build an OpenAI-compatible chat completion response object."""
    answer = answer or "No response generated."
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
    return response


async def _stream_answer(answer: str, response: dict[str, Any]):
    """Yield answer tokens as an SSE stream compatible with OpenAI clients."""
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

    question = _extract_question_from_messages(req.messages)
    answer = await _run_rag_with_timeout(question)
    logger.info("Answer: %s", answer[:200])

    response = _build_chat_response(answer)

    if req.stream:
        return StreamingResponse(_stream_answer(answer, response), media_type="text/event-stream")

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
            await asyncio.to_thread(embed, "warmup")
            logger.info("Embedding model warmed")
        except Exception as e:
            logger.warning("Embedding warmup failed: %s", e)

    async def warm_reranker():
        try:
            await asyncio.to_thread(rerank, "warmup", [{"payload": {"text": "warmup"}}])
            logger.info("Reranker warmed")
        except Exception as e:
            logger.warning("Reranker warmup failed: %s", e)

    await asyncio.gather(warm_llm(), warm_embed(), warm_reranker())

    logger.info("All models warmed")