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
import ipaddress
import logging
import threading
import time
import uuid
from collections.abc import AsyncIterator, Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any, Literal

import bcrypt as _bcrypt
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

import api.ollama_client as ollama_client
import api.retrieval
from api.embed import embed
from api.query_rag import ask, ask_stream_sync
from api.retrieval import Chunk, rerank
from settings import (
    ALLOW_INSECURE_LOCALONLY,
    API_KEY,
    CORS_ORIGINS,
    GEN_MODEL,
    JWT_SECRET,
    RAG_CONCURRENCY_LIMIT,
    RAG_EXECUTOR_WORKERS,
    RAG_REQUEST_TIMEOUT_SECONDS,
    STREAM_TIMEOUT_SECONDS,
    TRUSTED_PROXY_IPS,
)
from web import user_store
from web.auth import create_token, is_valid_token
from web.openai_compat import build_chat_response, make_stream_chunk, model_entry
from web.rate_limit import check_login_rate_limit, check_rate_limit
from web.schemas import (
    ChatRequest,
    LoginRequest,
    extract_question_from_messages,
    resolve_rag_mode,
    validate_chat_request,
)

logger = logging.getLogger(__name__)

_SERVER_START = int(time.time())
_WEB_DIR = Path(__file__).parent
# Precomputed sentinel so login always runs bcrypt regardless of whether the username exists,
# preventing timing-based username enumeration.
_DUMMY_HASH: bytes = _bcrypt.hashpw(b"__sentinel__", _bcrypt.gensalt())

def resolve_client_ip(request: Request) -> str:
    peer = request.client.host if request.client else "unknown"
    if peer in TRUSTED_PROXY_IPS:
        forwarded = request.headers.get("X-Forwarded-For", "")
        first = forwarded.split(",", 1)[0].strip()
        if first:
            try:
                ipaddress.ip_address(first)
                return first
            except ValueError:
                pass
    return peer


# Initialized in lifespan after the event loop is running.
_RAG_EXECUTOR: ThreadPoolExecutor
_RAG_CONCURRENCY: asyncio.Semaphore


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _RAG_EXECUTOR, _RAG_CONCURRENCY
    _RAG_EXECUTOR = ThreadPoolExecutor(max_workers=RAG_EXECUTOR_WORKERS)
    _RAG_CONCURRENCY = asyncio.Semaphore(RAG_CONCURRENCY_LIMIT)

    user_store.init_db()
    api.retrieval.startup()

    if not API_KEY and not JWT_SECRET:
        if not ALLOW_INSECURE_LOCALONLY:
            raise RuntimeError(
                "Authentication is required for the API. Set API_KEY or JWT_SECRET, "
                "or explicitly set ALLOW_INSECURE_LOCALONLY=true for local development."
            )
        logger.warning(
            "Authentication is DISABLED for local-only mode because "
            "ALLOW_INSECURE_LOCALONLY=true"
        )

    warm_task = asyncio.create_task(_warm_models())
    try:
        yield
    finally:
        warm_task.cancel()
        with suppress(asyncio.CancelledError):
            await warm_task
        _RAG_EXECUTOR.shutdown(wait=False)


app = FastAPI(title="Local RAG API", lifespan=lifespan)
app.mount("/ui", StaticFiles(directory=str(_WEB_DIR), html=True), name="ui")


@app.middleware("http")
async def security_middleware(request: Request, call_next: Callable[..., Any]):
    request_id = uuid.uuid4().hex[:12]
    request.state.request_id = request_id
    logger.info("[%s] %s %s", request_id, request.method, request.url.path)

    if request.url.path in ("/", "/favicon.ico") or request.url.path.startswith("/ui"):
        return await call_next(request)

    client_ip = resolve_client_ip(request)
    if request.url.path == "/auth/login":
        if not await check_login_rate_limit(client_ip):
            return JSONResponse(status_code=429, content={"detail": "Rate limit exceeded"})
        return await call_next(request)

    if not await check_rate_limit(client_ip):
        return JSONResponse(status_code=429, content={"detail": "Rate limit exceeded"})

    if API_KEY or JWT_SECRET:
        token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if not is_valid_token(token):
            return JSONResponse(status_code=401, content={"detail": "Unauthorized"})

    return await call_next(request)


app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization", "User-Agent"],
)


class _SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable[..., Any]) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'"
        )
        return response


app.add_middleware(_SecurityHeadersMiddleware)


async def _run_rag_with_timeout(
    question: str,
    model: str,
    rag_mode: Literal["strict", "augmented"] = "augmented",
    timeout: float = RAG_REQUEST_TIMEOUT_SECONDS,
) -> str:
    """Execute the RAG pipeline with a timeout and bounded in-flight work."""
    loop = asyncio.get_running_loop()
    await _RAG_CONCURRENCY.acquire()
    future = None
    try:
        future = loop.run_in_executor(_RAG_EXECUTOR, ask, question, model, rag_mode)
        future.add_done_callback(lambda _f: _RAG_CONCURRENCY.release())

        try:
            answer = await asyncio.wait_for(future, timeout=timeout)
        except TimeoutError:
            logger.warning("RAG pipeline timed out after %.1fs", timeout)
            raise HTTPException(
                status_code=504,
                detail="RAG pipeline timed out while generating an answer.",
            ) from None
        except Exception as e:
            logger.exception("RAG pipeline error")
            raise HTTPException(status_code=500, detail="RAG pipeline error") from e
    except BaseException:
        if future is None:
            _RAG_CONCURRENCY.release()
        raise

    return str(answer or "").strip()


async def _rag_stream_response(
    question: str,
    model: str,
    rag_mode: Literal["strict", "augmented"] = "augmented",
    http_request: Request | None = None,
) -> AsyncIterator[str]:
    """Bridge ask_stream_sync (sync generator) to an async SSE generator."""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[str | Exception | None] = asyncio.Queue()
    request_id = f"chatcmpl-{uuid.uuid4()}"
    created = int(time.time())
    cancel_event = threading.Event()

    def _run():
        try:
            for text in ask_stream_sync(question, model, rag_mode, cancel_event):
                loop.call_soon_threadsafe(queue.put_nowait, text)
        except Exception as exc:
            loop.call_soon_threadsafe(queue.put_nowait, exc)
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, None)

    await _RAG_CONCURRENCY.acquire()
    future = None
    try:
        future = loop.run_in_executor(_RAG_EXECUTOR, _run)
        future.add_done_callback(lambda _f: _RAG_CONCURRENCY.release())
    except BaseException:
        if future is None:
            _RAG_CONCURRENCY.release()
        raise

    async def _watch_disconnect():
        if http_request is None:
            return
        while not cancel_event.is_set():
            await asyncio.sleep(0.5)
            if await http_request.is_disconnected():
                cancel_event.set()
                logger.info("Client disconnected — cancelling stream")
                return

    disconnect_task = asyncio.create_task(_watch_disconnect())

    try:
        while True:
            item = await asyncio.wait_for(queue.get(), timeout=STREAM_TIMEOUT_SECONDS)
            if item is None:
                break
            if isinstance(item, Exception):
                logger.error("RAG stream error: %s", item)
                yield make_stream_chunk(
                    request_id, created, model, content="\n\n[Generation error — please retry]"
                )
                break
            yield make_stream_chunk(request_id, created, model, content=item)
    except TimeoutError:
        cancel_event.set()
        logger.warning("RAG stream timed out waiting for next chunk")
        yield make_stream_chunk(
            request_id, created, model, content="\n\n[Error: generation timed out]"
        )
    finally:
        cancel_event.set()
        disconnect_task.cancel()
        with suppress(asyncio.CancelledError):
            await disconnect_task

    yield make_stream_chunk(request_id, created, model, finish_reason="stop")
    yield "data: [DONE]\n\n"


@app.get("/v1/models")
def models():
    try:
        resp = ollama_client.get("/api/tags", timeout=5.0)
        resp.raise_for_status()
        data = [model_entry(m["name"], _SERVER_START) for m in resp.json().get("models", [])]
    except Exception:
        data = [model_entry(GEN_MODEL, _SERVER_START)]
    return {"object": "list", "data": data}


@app.get("/models")
def models_alias():
    return models()


@app.post("/v1/chat/completions")
async def chat(request: Request, req: ChatRequest):
    validate_chat_request(req)
    question = extract_question_from_messages(req.messages)
    rag_mode = resolve_rag_mode(req)

    if req.stream:
        return StreamingResponse(
            _rag_stream_response(question, req.model, rag_mode, request),
            media_type="text/event-stream",
        )

    answer = await _run_rag_with_timeout(question, req.model, rag_mode)
    return build_chat_response(answer, req.model)


@app.post("/chat/completions")
async def chat_alias(request: Request, req: ChatRequest):
    return await chat(request, req)


@app.get("/")
def root():
    return {"status": "rag-api running"}


@app.post("/auth/login")
async def login(credentials: LoginRequest) -> dict[str, str]:
    if not JWT_SECRET:
        raise HTTPException(status_code=503, detail="Web UI login not configured")
    stored = user_store.get_hash(credentials.username)
    # Always run bcrypt to prevent username enumeration via timing differences.
    hash_to_check = stored.encode() if stored else _DUMMY_HASH
    password_matches = await asyncio.to_thread(
        _bcrypt.checkpw, credentials.password.encode(), hash_to_check
    )
    if not stored or not password_matches:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_token(credentials.username)
    return {"token": token}


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
        _warm_one(
            "Reranker", rerank, "warmup",
            [Chunk(id="warmup", payload={"text": "warmup"}, score=1.0)],
        ),
    )
    logger.info("All models warmed")
