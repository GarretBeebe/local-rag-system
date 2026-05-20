"""Unit tests for API server executor and semaphore safety."""

import asyncio
from unittest.mock import MagicMock

import pytest


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
