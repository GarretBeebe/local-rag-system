"""
OpenAI-compatible chat completions endpoint backed by the local RAG pipeline.

Implements POST /v1/chat/completions so OpenAI-compatible clients
(Chatbox, Open WebUI, LangChain, etc.) can query the local knowledge base.

The server forwards the client's requested model to Ollama; any model
already pulled in Ollama can be selected per-request.

Run with:

    uvicorn web.api_server:app --host 0.0.0.0 --port 8000
"""

import asyncio
import json
import logging
import time
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, suppress
from collections.abc import Callable
from typing import Any, AsyncIterator, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

import api.ollama_client as ollama_client
from api.embed import embed
from api.query_rag import ask, ask_stream_sync
from api.retrieval import rerank
from settings import API_KEY, CORS_ORIGINS, GEN_MODEL

logger = logging.getLogger(__name__)

_SERVER_START = int(time.time())
_RAG_EXECUTOR = ThreadPoolExecutor(max_workers=4)
_RAG_CONCURRENCY = asyncio.Semaphore(4)

_RATE_WINDOW = 60.0
_RATE_MAX = 30
_rate_buckets: dict[str, list[float]] = defaultdict(list)
_rate_lock = asyncio.Lock()


async def _check_rate_limit(ip: str) -> bool:
    async with _rate_lock:
        now = time.monotonic()
        _rate_buckets[ip] = [t for t in _rate_buckets[ip] if now - t < _RATE_WINDOW]
        if len(_rate_buckets[ip]) >= _RATE_MAX:
            return False
        _rate_buckets[ip].append(now)
        return True


@asynccontextmanager
async def lifespan(app: FastAPI):
    warm_task = asyncio.create_task(_warm_models())
    try:
        yield
    finally:
        warm_task.cancel()
        with suppress(asyncio.CancelledError):
            await warm_task
        _RAG_EXECUTOR.shutdown(wait=False)


app = FastAPI(title="Local RAG API", lifespan=lifespan)


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    logger.info("%s %s", request.method, request.url.path)

    # Health check bypasses auth and rate limiting so Docker healthcheck works
    if request.url.path == "/" and request.method == "GET":
        return await call_next(request)

    client_ip = request.client.host if request.client else "unknown"
    if not await _check_rate_limit(client_ip):
        return JSONResponse(status_code=429, content={"detail": "Rate limit exceeded"})

    if API_KEY:
        auth = request.headers.get("Authorization", "")
        if auth != f"Bearer {API_KEY}":
            return JSONResponse(status_code=401, content={"detail": "Unauthorized"})

    return await call_next(request)


# Registered after security_middleware so CORS is the outermost layer —
# this ensures CORS headers appear on 401/429 responses too.
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization"],
)


class ChatMessage(BaseModel):
    role: Literal["user", "assistant", "system", "tool"]
    content: str | list[dict[str, str]]


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


async def _run_rag_with_timeout(question: str, model: str, timeout: float = 240.0) -> str:
    """Execute the RAG pipeline with a timeout and bounded in-flight work.

    The semaphore counts in-flight executor tasks, not just requests waiting
    on them. We therefore:

    - Acquire the semaphore before submitting to the executor.
    - Attach a done-callback that releases the semaphore when the future
      actually completes, regardless of who is awaiting it.
    - On timeout, we *do not* release the semaphore early; the slot only
      becomes available when the underlying work finishes.
    """
    loop = asyncio.get_running_loop()
    await _RAG_CONCURRENCY.acquire()
    future = None
    try:
        future = loop.run_in_executor(_RAG_EXECUTOR, ask, question, model)
        future.add_done_callback(lambda _f: _RAG_CONCURRENCY.release())

        try:
            answer = await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning("RAG pipeline timed out after %.1fs", timeout)
            raise HTTPException(
                status_code=504,
                detail="RAG pipeline timed out while generating an answer.",
            ) from None
        except Exception as e:
            logger.exception("RAG pipeline error")
            raise HTTPException(status_code=500, detail="RAG pipeline error") from e
    except BaseException:
        # Only release here if we failed before creating the future/callback,
        # otherwise the done-callback owns releasing the permit.
        if future is None:
            _RAG_CONCURRENCY.release()
        raise

    return str(answer or "").strip()


async def _rag_stream_response(question: str, model: str) -> AsyncIterator[str]:
    """Bridge ask_stream_sync (sync generator) to an async SSE generator.

    Runs ask_stream_sync in the thread pool and forwards text chunks through
    an asyncio.Queue so the async event loop can yield them to the client as
    they arrive from Ollama — giving true time-to-first-token streaming.
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[str | Exception | None] = asyncio.Queue()
    request_id = f"chatcmpl-{uuid.uuid4()}"
    created = int(time.time())

    def _run():
        try:
            for text in ask_stream_sync(question, model):
                loop.call_soon_threadsafe(queue.put_nowait, text)
        except Exception as exc:
            loop.call_soon_threadsafe(queue.put_nowait, exc)
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, None)

    await _RAG_CONCURRENCY.acquire()
    future = loop.run_in_executor(_RAG_EXECUTOR, _run)
    future.add_done_callback(lambda _f: _RAG_CONCURRENCY.release())

    try:
        while True:
            item = await asyncio.wait_for(queue.get(), timeout=120.0)
            if item is None:
                break
            if isinstance(item, Exception):
                logger.exception("RAG stream error: %s", item)
                break
            chunk = {
                "id": request_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{"index": 0, "delta": {"content": item}, "finish_reason": None}],
            }
            yield f"data: {json.dumps(chunk)}\n\n"
    except asyncio.TimeoutError:
        logger.warning("RAG stream timed out waiting for next chunk")

    done_chunk = {
        "id": request_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }
    yield f"data: {json.dumps(done_chunk)}\n\n"
    yield "data: [DONE]\n\n"


def _build_chat_response(answer: str, model: str) -> dict[str, Any]:
    """Build an OpenAI-compatible chat completion response object."""
    answer = answer or "No response generated."
    response = {
        "id": f"chatcmpl-{uuid.uuid4()}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
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


def _model_entry(model_id: str) -> dict[str, Any]:
    return {"id": model_id, "object": "model", "created": _SERVER_START, "owned_by": "ollama"}


@app.get("/v1/models")
def models():
    try:
        resp = ollama_client.get("/api/tags", timeout=5.0)
        resp.raise_for_status()
        data = [_model_entry(m["name"]) for m in resp.json().get("models", [])]
    except Exception:
        data = [_model_entry(GEN_MODEL)]
    return {"object": "list", "data": data}


@app.get("/models")
def models_alias():
    return models()


@app.post("/v1/chat/completions")
async def chat(req: ChatRequest):
    question = _extract_question_from_messages(req.messages)
    model = req.model

    if req.stream:
        return StreamingResponse(
            _rag_stream_response(question, model),
            media_type="text/event-stream",
        )

    answer = await _run_rag_with_timeout(question, model)
    logger.info("Answer: %s", answer[:200])
    return _build_chat_response(answer, model)


@app.post("/chat/completions")
async def chat_alias(req: ChatRequest):
    return await chat(req)


@app.get("/")
def root():
    return {"status": "rag-api running"}


async def _warm_one(name: str, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
    try:
        await asyncio.to_thread(fn, *args, **kwargs)
        logger.info("%s warmed", name)
    except Exception as e:
        logger.warning("%s warmup failed: %s", name, e)


async def _warm_models() -> None:
    logger.info("Warming RAG models...")
    await asyncio.gather(
        _warm_one("LLM", ollama_client.post, "/api/generate",
                  json={"model": GEN_MODEL, "prompt": "warmup", "stream": False}, timeout=60),
        _warm_one("Embedding model", embed, "warmup"),
        _warm_one("Reranker", rerank, "warmup", [{"payload": {"text": "warmup"}}]),
    )
    logger.info("All models warmed")
