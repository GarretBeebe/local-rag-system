"""Unit tests for API server executor and semaphore safety."""

import asyncio
import concurrent.futures
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException


def test_get_rag_executor_before_lifespan_raises_runtime_error():
    import web.api_server as srv
    srv._RAG_EXECUTOR = None
    with pytest.raises(RuntimeError, match="lifespan not started"):
        srv._get_rag_executor()


def test_get_rag_concurrency_before_lifespan_raises_runtime_error():
    import web.api_server as srv
    srv._RAG_CONCURRENCY = None
    with pytest.raises(RuntimeError, match="lifespan not started"):
        srv._get_rag_concurrency()


def test_semaphore_released_when_executor_scheduling_fails():
    """A failure inside run_in_executor before the future is assigned must release
    the semaphore so the concurrency slot is not permanently leaked."""
    import web.api_server as srv

    async def run():
        sem = asyncio.Semaphore(1)
        # These globals are set by lifespan at runtime; wire them up directly for tests.
        srv._RAG_CONCURRENCY = sem

        bad_executor = MagicMock()
        bad_executor.submit.side_effect = RuntimeError("forced executor failure")
        srv._RAG_EXECUTOR = bad_executor

        gen = srv._rag_stream_response("q", "model")
        with pytest.raises(RuntimeError, match="forced executor failure"):
            await gen.__anext__()

        # Slot must be available again — value of 1 means the semaphore is free.
        assert sem._value == 1

    asyncio.run(run())


def test_run_rag_with_timeout_raises_504_when_capacity_unavailable():
    """When all semaphore slots are occupied, _run_rag_with_timeout must raise
    HTTPException(504) rather than blocking indefinitely."""
    import web.api_server as srv

    async def run():
        sem = asyncio.Semaphore(1)
        await sem.acquire()  # fill the only slot
        srv._RAG_CONCURRENCY = sem
        srv._RAG_EXECUTOR = MagicMock()

        with pytest.raises(HTTPException) as exc_info:
            await srv._run_rag_with_timeout("q", "model", timeout=0.05)
        assert exc_info.value.status_code == 504
        assert "capacity" in exc_info.value.detail

        sem.release()

    asyncio.run(run())


def test_run_rag_with_timeout_raises_504_when_budget_exhausted_after_acquire(monkeypatch):
    """Semaphore acquired but elapsed time already consumed the total budget must
    return HTTPException(504), not a raw TimeoutError, and must release the slot.

    Patches srv.time (the module reference inside api_server.py) rather than the
    global time.monotonic, so asyncio's own internal time calls are unaffected.
    """
    import time as real_time_mod
    import web.api_server as srv

    class _FakeTime:
        _call_n = 0
        _real_mono = staticmethod(real_time_mod.monotonic)

        def monotonic(self):
            self._call_n += 1
            if self._call_n == 1:   # 'started = time.monotonic()'
                return 0.0
            if self._call_n == 2:   # 'time.monotonic()' in remaining calc
                return 10_000.0     # → remaining = timeout - 10000 ≤ 0
            return self._real_mono()

        def __getattr__(self, name):
            return getattr(real_time_mod, name)

    async def run():
        sem = asyncio.Semaphore(1)
        srv._RAG_CONCURRENCY = sem
        srv._RAG_EXECUTOR = MagicMock()

        monkeypatch.setattr(srv, "time", _FakeTime())

        with pytest.raises(HTTPException) as exc_info:
            await srv._run_rag_with_timeout("q", "model", timeout=10.0)
        assert exc_info.value.status_code == 504
        assert "capacity" in exc_info.value.detail
        assert sem._value == 1  # released via outer except (future was None)

    asyncio.run(run())


def test_semaphore_released_via_done_callback_after_generation_timeout(monkeypatch):
    """When asyncio.wait_for times out during generation the done callback on the
    underlying concurrent.futures.Future must still release the semaphore slot."""
    import time as real_time_mod
    import web.api_server as srv

    class _FakeTime:
        _call_n = 0
        _real_mono = staticmethod(real_time_mod.monotonic)

        def monotonic(self):
            self._call_n += 1
            if self._call_n == 1:   # 'started'
                return 0.0
            if self._call_n == 2:   # remaining check → 0.99s elapsed of 1.0s budget
                return 0.99
            return self._real_mono()

        def __getattr__(self, name):
            return getattr(real_time_mod, name)

    async def run():
        sem = asyncio.Semaphore(1)
        srv._RAG_CONCURRENCY = sem

        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        srv._RAG_EXECUTOR = executor

        def slow_ask(*args):
            import time as _t
            _t.sleep(0.3)
            return "answer"

        monkeypatch.setattr(srv, "ask", slow_ask)
        # remaining = 1.0 - 0.99 = 0.01s; slow_ask takes 0.3s → wait_for times out
        monkeypatch.setattr(srv, "time", _FakeTime())

        with pytest.raises(HTTPException) as exc_info:
            await srv._run_rag_with_timeout("q", "model", timeout=1.0)
        assert exc_info.value.status_code == 504

        # Let the thread finish so the done callback fires and releases the semaphore.
        await asyncio.sleep(0.4)
        assert sem._value == 1

        executor.shutdown(wait=False)

    asyncio.run(run())
