"""Unit tests for streaming helper functions extracted from _rag_stream_response."""

import asyncio
import threading
import types
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor


async def _collect(gen: AsyncIterator[str]) -> list[str]:
    items: list[str] = []
    async for item in gen:
        items.append(item)
    return items


def _setup_server(monkeypatch) -> tuple[types.ModuleType, asyncio.Semaphore, ThreadPoolExecutor]:
    import web.api_server as srv

    sem = asyncio.Semaphore(10)
    executor = ThreadPoolExecutor(max_workers=2)
    srv._RAG_CONCURRENCY = sem
    srv._RAG_EXECUTOR = executor
    return srv, sem, executor


def test_happy_path_yields_content_then_done(monkeypatch):
    """Streaming a normal response yields content chunks followed by stop and DONE."""

    async def run():
        srv, _, executor = _setup_server(monkeypatch)
        monkeypatch.setattr(
            srv, "ask_stream_sync", lambda q, m, r, cancel: iter(["hello ", "world"])
        )

        chunks = await _collect(srv._rag_stream_response("q", "model"))

        executor.shutdown(wait=False)

        assert "hello " in "".join(chunks)
        assert "world" in "".join(chunks)
        assert any("stop" in c for c in chunks)
        assert any("[DONE]" in c for c in chunks)

    asyncio.run(run())


def test_generator_exception_yields_error_chunk_then_done(monkeypatch):
    """An exception from ask_stream_sync yields a generation-error SSE chunk then stop+DONE."""

    def bad_stream(q, m, r, cancel):
        raise RuntimeError("generation blew up")
        yield  # make it a generator

    async def run():
        srv, _, executor = _setup_server(monkeypatch)
        monkeypatch.setattr(srv, "ask_stream_sync", bad_stream)

        chunks = await _collect(srv._rag_stream_response("q", "model"))

        executor.shutdown(wait=False)

        combined = "".join(chunks)
        assert "Generation error" in combined
        assert any("stop" in c for c in chunks)
        assert any("[DONE]" in c for c in chunks)

    asyncio.run(run())


def test_queue_timeout_yields_timeout_chunk(monkeypatch):
    """When the queue produces no item within the timeout, a timeout error chunk is emitted."""
    import time

    def hanging_stream(q, m, r, cancel):
        time.sleep(5)
        yield "never"

    async def run():
        srv, _, executor = _setup_server(monkeypatch)
        monkeypatch.setattr(srv, "ask_stream_sync", hanging_stream)
        monkeypatch.setattr(srv, "STREAM_TIMEOUT_SECONDS", 0.05)

        chunks = await _collect(srv._rag_stream_response("q", "model"))

        executor.shutdown(wait=False)

        combined = "".join(chunks)
        assert "timed out" in combined or "generation timed out" in combined

    asyncio.run(run())


def test_disconnect_sets_cancel_event():
    """_watch_disconnect sets cancel_event when the client disconnects."""
    from web.api_server import _watch_disconnect

    cancel_event = threading.Event()

    class MockRequest:
        async def is_disconnected(self):
            return True

    async def run():
        await _watch_disconnect(MockRequest(), cancel_event)

    asyncio.run(run())
    assert cancel_event.is_set()
