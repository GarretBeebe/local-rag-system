"""Sliding-window in-memory rate limiter."""

import asyncio
import time
from collections import defaultdict

from settings import RATE_MAX_LOGIN_REQUESTS, RATE_MAX_REQUESTS, RATE_WINDOW_SECONDS

LOGIN_RATE_MAX = RATE_MAX_LOGIN_REQUESTS

_rate_buckets: dict[str, list[float]] = defaultdict(list)
_login_rate_buckets: dict[str, list[float]] = defaultdict(list)
_rate_lock = asyncio.Lock()


async def check_rate_limit(
    ip: str,
    *,
    buckets: dict[str, list[float]] = _rate_buckets,
    max_requests: int = RATE_MAX_REQUESTS,
) -> bool:
    """Return True if the request is within rate limits, False if it should be rejected."""
    async with _rate_lock:
        now = time.monotonic()
        buckets[ip] = [t for t in buckets[ip] if now - t < RATE_WINDOW_SECONDS]
        if len(buckets[ip]) >= max_requests:
            return False
        buckets[ip].append(now)
        return True


async def check_login_rate_limit(ip: str) -> bool:
    """Return True if the login attempt is within the login rate limit."""
    return await check_rate_limit(ip, buckets=_login_rate_buckets, max_requests=LOGIN_RATE_MAX)
