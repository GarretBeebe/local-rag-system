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
import secrets
import threading
import time
import uuid
from collections.abc import AsyncIterator, Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any

import bcrypt as _bcrypt
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

import api.ollama_client as ollama_client
import api.retrieval
from api.embed import embed
from api.query_rag import ask, ask_stream_sync
from api.retrieval import Chunk, rerank, retrieve_best
from common.types import RagMode
from settings import (
    ALLOW_INSECURE_LOCALONLY,
    CORS_ORIGINS,
    GEN_MODEL,
    OLLAMA_MODEL_LIST_TIMEOUT_SECONDS,
    OLLAMA_WARMUP_TIMEOUT_SECONDS,
    RAG_CONCURRENCY_LIMIT,
    RAG_EXECUTOR_WORKERS,
    RAG_INTERNAL_TOKEN,
    RAG_REQUEST_TIMEOUT_SECONDS,
    SESSION_EXPIRY_HOURS,
    STREAM_TIMEOUT_SECONDS,
    TRUSTED_PROXY_IPS,
    WARM_MODELS_ON_STARTUP,
)
from web import user_store
from web.auth import create_session, is_valid_token, revoke_session
from web.openai_compat import build_chat_response, make_stream_chunk, model_entry
from web.rate_limit import check_login_rate_limit, check_rate_limit, start_sweep_tasks
from web.schemas import (
    ChatRequest,
    LoginRequest,
    RetrieveRequest,
    extract_question_from_messages,
    resolve_rag_mode,
    validate_chat_request,
)

logger = logging.getLogger(__name__)

_SERVER_START = int(time.time())
_WEB_DIR = Path(__file__).parent
_STATIC_DIR = _WEB_DIR / "static"
_AUTH_COOKIE = "rag_token"
_DISCONNECT_POLL_SECONDS = 2.0
_RAG_CAPACITY_TIMEOUT_DETAIL = "RAG pipeline timed out waiting for capacity."
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
_RAG_EXECUTOR: ThreadPoolExecutor | None = None
_RAG_CONCURRENCY: asyncio.Semaphore | None = None


def _get_rag_executor() -> ThreadPoolExecutor:
    if _RAG_EXECUTOR is None:
        raise RuntimeError("RAG executor has not been initialized — lifespan not started")
    return _RAG_EXECUTOR


def _get_rag_concurrency() -> asyncio.Semaphore:
    if _RAG_CONCURRENCY is None:
        raise RuntimeError(
            "RAG concurrency limiter has not been initialized — lifespan not started"
        )
    return _RAG_CONCURRENCY


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    global _RAG_EXECUTOR, _RAG_CONCURRENCY
    _RAG_EXECUTOR = ThreadPoolExecutor(max_workers=RAG_EXECUTOR_WORKERS)
    _RAG_CONCURRENCY = asyncio.Semaphore(RAG_CONCURRENCY_LIMIT)

    user_store.init_db()
    try:
        user_store.purge_expired_sessions()
    except Exception as exc:
        logger.warning("Failed to purge expired sessions on startup: %s", exc)
    api.retrieval.startup()

    if ALLOW_INSECURE_LOCALONLY:
        logger.warning(
            "Authentication is DISABLED for local-only mode because ALLOW_INSECURE_LOCALONLY=true"
        )

    warm_task = asyncio.create_task(_warm_models()) if WARM_MODELS_ON_STARTUP else None
    sweep_tasks = await start_sweep_tasks()
    try:
        yield
    finally:
        if warm_task is not None:
            warm_task.cancel()
        for t in sweep_tasks:
            t.cancel()
        if warm_task is not None:
            with suppress(asyncio.CancelledError):
                await warm_task
        for t in sweep_tasks:
            with suppress(asyncio.CancelledError):
                await t
        api.retrieval.shutdown()
        futs = [
            _get_rag_executor().submit(ollama_client.close_session)
            for _ in range(RAG_EXECUTOR_WORKERS)
        ]
        for f in futs:
            with suppress(Exception):
                f.result(timeout=2)
        _get_rag_executor().shutdown(wait=True)


app = FastAPI(title="Local RAG API", lifespan=lifespan)
app.mount("/ui", StaticFiles(directory=str(_STATIC_DIR), html=True), name="ui")


def _extract_bearer_token(request: Request) -> str:
    token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    return token or request.cookies.get(_AUTH_COOKIE, "")


@app.middleware("http")
async def security_middleware(request: Request, call_next: Callable[..., Any]) -> Response:
    request_id = uuid.uuid4().hex[:12]
    request.state.request_id = request_id
    logger.info("[%s] %s %s", request_id, request.method, request.url.path)

    if request.url.path == "/favicon.ico" or request.url.path.startswith("/ui"):
        return await call_next(request)
    if request.url.path == "/healthz":
        return await call_next(request)

    client_ip = resolve_client_ip(request)
    if request.url.path == "/auth/login":
        if not await check_login_rate_limit(client_ip):
            return JSONResponse(status_code=429, content={"detail": "Rate limit exceeded"})
        return await call_next(request)
    if request.url.path == "/auth/logout":
        return await call_next(request)

    if not await check_rate_limit(client_ip):
        return JSONResponse(status_code=429, content={"detail": "Rate limit exceeded"})

    if request.url.path == "/auth/status":
        return await call_next(request)
    if request.url.path == "/v1/retrieve":
        return await call_next(request)

    if not ALLOW_INSECURE_LOCALONLY and not is_valid_token(_extract_bearer_token(request)):
        return JSONResponse(status_code=401, content={"detail": "Unauthorized"})

    return await call_next(request)


app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization", "User-Agent"],
)


@app.middleware("http")
async def _security_headers_middleware(request: Request, call_next: Callable[..., Any]) -> Response:
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self'; "
        "base-uri 'self'; "
        "frame-ancestors 'none'"
    )
    return response


async def _run_rag_with_timeout(
    question: str,
    model: str,
    rag_mode: RagMode = "augmented",
    timeout: float = RAG_REQUEST_TIMEOUT_SECONDS,
) -> str:
    """Execute the RAG pipeline with a timeout and bounded in-flight work."""
    cancel_event = threading.Event()
    # shield: timeout cancels the wrapper, not the executor future, so the
    # done callback releases capacity only when ask() truly exits.
    future, remaining = await _acquire_and_submit(
        lambda: ask(question, model, rag_mode, cancel_event), timeout
    )
    try:
        answer = await asyncio.wait_for(asyncio.shield(future), timeout=remaining)
        return answer.strip()
    except TimeoutError:
        cancel_event.set()
        logger.warning("RAG pipeline timed out after %.1fs", timeout)
        raise HTTPException(
            status_code=504,
            detail="RAG pipeline timed out while generating an answer.",
        ) from None
    except Exception as e:
        cancel_event.set()
        logger.exception("RAG pipeline error")
        raise HTTPException(status_code=500, detail="RAG pipeline error") from e


async def _wait_for_capacity(timeout: float) -> asyncio.Semaphore:
    """Acquire the RAG semaphore, raising TimeoutError if capacity is not available in time."""
    semaphore = _get_rag_concurrency()
    await asyncio.wait_for(semaphore.acquire(), timeout=timeout)
    return semaphore


async def _acquire_rag_capacity(timeout: float) -> asyncio.Semaphore:
    try:
        return await _wait_for_capacity(timeout)
    except TimeoutError:
        logger.warning("RAG pipeline timed out waiting for capacity after %.1fs", timeout)
        raise HTTPException(status_code=504, detail=_RAG_CAPACITY_TIMEOUT_DETAIL) from None


def _submit_rag_job(
    loop: asyncio.AbstractEventLoop,
    semaphore: asyncio.Semaphore,
    fn: Callable[..., Any],
    *args: Any,
) -> asyncio.Future[Any]:
    try:
        future = loop.run_in_executor(_get_rag_executor(), fn, *args)
    except BaseException:
        # BaseException catches CancelledError (not a subclass of Exception) so the
        # semaphore is always released even if the event loop is shutting down.
        semaphore.release()
        raise
    future.add_done_callback(lambda _f: semaphore.release())
    return future


async def _acquire_and_submit(
    fn: Callable[[], Any],
    timeout: float = RAG_REQUEST_TIMEOUT_SECONDS,
) -> tuple[asyncio.Future[Any], float]:
    """Acquire the RAG semaphore, compute remaining time, and submit fn to the executor.

    Returns (future, remaining_seconds). Raises HTTPException(504) if capacity is
    unavailable or the budget is already exhausted after acquisition.
    """
    started = time.monotonic()
    semaphore = await _acquire_rag_capacity(timeout)
    remaining = timeout - (time.monotonic() - started)
    if remaining <= 0:
        semaphore.release()
        raise HTTPException(status_code=504, detail=_RAG_CAPACITY_TIMEOUT_DETAIL)
    return _submit_rag_job(asyncio.get_running_loop(), semaphore, fn), remaining


async def _start_stream_worker(
    question: str,
    model: str,
    rag_mode: RagMode,
    loop: asyncio.AbstractEventLoop,
) -> tuple[asyncio.Queue[str | Exception | None], threading.Event, asyncio.Future[Any]]:
    """Acquire the semaphore, schedule the stream worker, and return (queue, cancel_event, future).

    Raises TimeoutError if the semaphore cannot be acquired within RAG_REQUEST_TIMEOUT_SECONDS.
    """
    queue: asyncio.Queue[str | Exception | None] = asyncio.Queue(maxsize=32)
    cancel_event = threading.Event()

    def _run():
        try:
            for text in ask_stream_sync(question, model, rag_mode, cancel_event):
                coro = queue.put(text)
                try:
                    asyncio.run_coroutine_threadsafe(coro, loop).result()
                except RuntimeError:
                    coro.close()  # loop closed; prevent "coroutine never awaited" warning
                    return
        except Exception as exc:
            with suppress(RuntimeError):
                loop.call_soon_threadsafe(queue.put_nowait, exc)
        finally:
            with suppress(RuntimeError):
                loop.call_soon_threadsafe(queue.put_nowait, None)

    semaphore = await _wait_for_capacity(RAG_REQUEST_TIMEOUT_SECONDS)
    future = _submit_rag_job(loop, semaphore, _run)
    return queue, cancel_event, future


async def _watch_disconnect(request: Request, cancel_event: threading.Event) -> None:
    """Poll for client disconnect and set cancel_event when detected."""
    while not cancel_event.is_set():
        await asyncio.sleep(_DISCONNECT_POLL_SECONDS)
        if await request.is_disconnected():
            cancel_event.set()
            logger.info("Client disconnected — cancelling stream")
            return


async def _stream_queue_events(
    queue: asyncio.Queue[str | Exception | None],
    cancel_event: threading.Event,
    request_id: str,
    created: int,
    model: str,
) -> AsyncIterator[str]:
    """Drain the worker queue, mapping exceptions and timeouts to SSE error chunks."""
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


async def _rag_stream_response(
    question: str,
    model: str,
    rag_mode: RagMode = "augmented",
    http_request: Request | None = None,
) -> AsyncIterator[str]:
    """Bridge ask_stream_sync (sync generator) to an async SSE generator."""
    loop = asyncio.get_running_loop()
    request_id = f"chatcmpl-{uuid.uuid4()}"
    created = int(time.time())

    try:
        queue, cancel_event, _ = await _start_stream_worker(question, model, rag_mode, loop)
    except TimeoutError:
        logger.warning("RAG stream timed out waiting for capacity")
        yield make_stream_chunk(
            request_id, created, model, content="\n\n[Error: server at capacity, please retry]"
        )
        yield make_stream_chunk(request_id, created, model, finish_reason="stop")
        yield "data: [DONE]\n\n"
        return

    disconnect_task = (
        asyncio.create_task(_watch_disconnect(http_request, cancel_event))
        if http_request is not None
        else None
    )

    try:
        async for chunk in _stream_queue_events(queue, cancel_event, request_id, created, model):
            yield chunk
    finally:
        cancel_event.set()
        if disconnect_task is not None:
            disconnect_task.cancel()
            with suppress(asyncio.CancelledError):
                await disconnect_task

    yield make_stream_chunk(request_id, created, model, finish_reason="stop")
    yield "data: [DONE]\n\n"


def _check_internal_token(request: Request) -> None:
    if RAG_INTERNAL_TOKEN is None:
        raise HTTPException(status_code=503, detail="Retrieve endpoint not configured")
    token = _extract_bearer_token(request)
    if not secrets.compare_digest(token, RAG_INTERNAL_TOKEN):
        raise HTTPException(status_code=401, detail="Unauthorized")


@app.post("/v1/retrieve")
async def retrieve(request: Request, req: RetrieveRequest) -> dict[str, Any]:
    _check_internal_token(request)
    future, remaining = await _acquire_and_submit(
        lambda: retrieve_best(req.query, final_k=req.limit)
    )
    try:
        chunks = await asyncio.wait_for(asyncio.shield(future), timeout=remaining)
    except TimeoutError:
        logger.warning("RAG retrieve timed out after %.1fs", RAG_REQUEST_TIMEOUT_SECONDS)
        raise HTTPException(status_code=504, detail="RAG pipeline timed out") from None
    except Exception as e:
        logger.exception("RAG retrieve error")
        raise HTTPException(status_code=500, detail="RAG pipeline error") from e
    return {
        "chunks": [
            {
                "text": c.payload.get("text", ""),
                "filepath": c.payload.get("filepath", ""),
                "score": c.rerank_score if c.rerank_score is not None else c.score,
            }
            for c in chunks
        ]
    }


@app.get("/v1/models")
@app.get("/models")
def models() -> dict[str, Any]:
    try:
        resp = ollama_client.get("/api/tags", timeout=OLLAMA_MODEL_LIST_TIMEOUT_SECONDS)
        resp.raise_for_status()
        data = [model_entry(m["name"], _SERVER_START) for m in resp.json().get("models", [])]
    except Exception:
        logger.warning("Failed to list Ollama models, returning default")
        data = [model_entry(GEN_MODEL, _SERVER_START)]
    return {"object": "list", "data": data}


@app.post("/v1/chat/completions")
@app.post("/chat/completions")
async def chat(request: Request, req: ChatRequest) -> Response:
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


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
def root() -> RedirectResponse:
    return RedirectResponse(url="/ui/")


@app.post("/auth/login")
async def login(request: Request, response: Response, credentials: LoginRequest) -> dict[str, bool]:
    stored = user_store.get_hash(credentials.username)
    # Always run bcrypt to prevent username enumeration via timing differences.
    hash_to_check = stored.encode() if stored else _DUMMY_HASH
    password_matches = await asyncio.to_thread(
        _bcrypt.checkpw, credentials.password.encode(), hash_to_check
    )
    if not stored or not password_matches:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_session(credentials.username)
    is_secure = (
        request.url.scheme == "https"
        or request.headers.get("X-Forwarded-Proto", "").split(",", 1)[0].strip() == "https"
    )
    response.set_cookie(
        _AUTH_COOKIE,
        token,
        httponly=True,
        secure=is_secure,
        samesite="lax",
        max_age=SESSION_EXPIRY_HOURS * 3600,
        path="/",
    )
    return {"ok": True}


@app.post("/auth/logout")
async def logout(request: Request, response: Response) -> dict[str, bool]:
    token = request.cookies.get(_AUTH_COOKIE, "")
    if token:
        revoke_session(token)
    response.delete_cookie(_AUTH_COOKIE, path="/")
    return {"ok": True}


@app.get("/auth/status")
def auth_status(request: Request) -> Response:
    authenticated = ALLOW_INSECURE_LOCALONLY or is_valid_token(_extract_bearer_token(request))
    return JSONResponse(content={"authenticated": authenticated})


async def _warm_one(name: str, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
    try:
        await asyncio.to_thread(fn, *args, **kwargs)
        logger.info("%s warmed", name)
    except Exception as e:
        logger.warning("%s warmup failed: %s", name, e)


async def _warm_models() -> None:
    logger.info("Warming RAG models...")
    await asyncio.gather(
        _warm_one(
            "LLM",
            ollama_client.post,
            "/api/generate",
            json={"model": GEN_MODEL, "prompt": "warmup", "stream": False},
            timeout=OLLAMA_WARMUP_TIMEOUT_SECONDS,
        ),
        _warm_one("Embedding model", embed, "warmup"),
        _warm_one(
            "Reranker",
            rerank,
            "warmup",
            [Chunk(id="warmup", payload={"text": "warmup"}, score=1.0)],
        ),
    )
    logger.info("All models warmed")
