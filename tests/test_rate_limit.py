"""Unit tests for the sliding-window rate limiter."""

import asyncio
import time

import pytest

import web.rate_limit as rl
from web.rate_limit import check_login_rate_limit, check_rate_limit


@pytest.fixture(autouse=True)
def fresh_buckets():
    """Clear the live bucket dicts so each test starts with a clean slate.

    check_rate_limit's `buckets` default arg is bound to the original dict
    objects at definition time, so we clear them rather than replace them.
    """
    rl._rate_buckets.clear()
    rl._login_rate_buckets.clear()
    yield
    rl._rate_buckets.clear()
    rl._login_rate_buckets.clear()


# --- check_rate_limit ---

def test_first_request_passes():
    assert asyncio.run(check_rate_limit("1.2.3.4")) is True


def test_requests_under_limit_pass():
    async def run():
        for _ in range(5):
            assert await check_rate_limit("1.2.3.4", max_requests=10) is True

    asyncio.run(run())


def test_request_at_limit_is_rejected():
    async def run():
        for _ in range(3):
            await check_rate_limit("1.2.3.4", max_requests=3)
        return await check_rate_limit("1.2.3.4", max_requests=3)

    assert asyncio.run(run()) is False


def test_different_ips_have_independent_limits():
    async def run():
        for _ in range(3):
            await check_rate_limit("1.1.1.1", max_requests=3)
        return await check_rate_limit("2.2.2.2", max_requests=3)

    assert asyncio.run(run()) is True


def test_window_expiry_allows_new_requests(monkeypatch):
    monkeypatch.setattr(rl, "RATE_WINDOW_SECONDS", 60.0)

    async def run():
        for _ in range(3):
            await check_rate_limit("1.2.3.4", max_requests=3)

        # Backdate all timestamps to outside the window by writing directly into
        # the original bucket dict (the same object the default arg captures).
        old_time = time.monotonic() - 61.0
        rl._rate_buckets["1.2.3.4"] = [old_time, old_time, old_time]

        return await check_rate_limit("1.2.3.4", max_requests=3)

    assert asyncio.run(run()) is True


# --- check_login_rate_limit ---

def test_login_first_attempt_passes():
    assert asyncio.run(check_login_rate_limit("1.2.3.4")) is True


def test_login_at_limit_is_rejected(monkeypatch):
    monkeypatch.setattr(rl, "LOGIN_RATE_MAX", 2)

    async def run():
        await check_login_rate_limit("1.2.3.4")
        await check_login_rate_limit("1.2.3.4")
        return await check_login_rate_limit("1.2.3.4")

    assert asyncio.run(run()) is False


def test_login_does_not_affect_general_bucket():
    async def run():
        for _ in range(5):
            await check_login_rate_limit("1.2.3.4")
        return await check_rate_limit("1.2.3.4", max_requests=10)

    assert asyncio.run(run()) is True
