"""Timing helpers for RAG pipeline stages, enabled by RAG_TIMING."""

import logging
import time
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

from settings import RAG_TIMING

logger = logging.getLogger(__name__)


@contextmanager
def timed(label: str) -> Generator[None, None, None]:
    if not RAG_TIMING:
        yield
        return
    start = time.perf_counter()
    try:
        yield
    finally:
        logger.info("%s: %.3fs", label, time.perf_counter() - start)


def log_generation_stats(data: dict[str, Any]) -> None:
    """Log Ollama's own timings from a finished generation (durations are nanoseconds).

    load = model load (a cold start when large), prompt = prefill of the RAG context.
    """
    if not RAG_TIMING:
        return
    logger.info(
        "ollama: load=%.2fs prompt=%s tokens/%.2fs output=%s tokens/%.2fs",
        data.get("load_duration", 0) / 1e9,
        data.get("prompt_eval_count", "?"),
        data.get("prompt_eval_duration", 0) / 1e9,
        data.get("eval_count", "?"),
        data.get("eval_duration", 0) / 1e9,
    )
