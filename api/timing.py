"""Timing context manager for RAG pipeline stages."""

import logging
import time
from collections.abc import Generator
from contextlib import contextmanager

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
        logger.debug("%s: %.3fs", label, time.perf_counter() - start)
