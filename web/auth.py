"""Token creation and validation for API key and JWT bearer auth."""

import hmac
from datetime import UTC, datetime, timedelta

import jwt as pyjwt

from settings import API_KEY, JWT_EXPIRY_HOURS, JWT_SECRET
from web import user_store


def create_token(username: str) -> str:
    """Create a signed JWT for the given username."""
    exp = datetime.now(UTC) + timedelta(hours=JWT_EXPIRY_HOURS)
    return pyjwt.encode({"sub": username, "exp": exp}, JWT_SECRET, algorithm="HS256")


def is_valid_token(token: str) -> bool:
    """Return True if token is a valid API key or JWT for an existing user."""
    if API_KEY and hmac.compare_digest(token, API_KEY):
        return True
    if JWT_SECRET:
        try:
            payload = pyjwt.decode(token, JWT_SECRET, algorithms=["HS256"])
            return user_store.get_hash(payload.get("sub", "")) is not None
        except pyjwt.InvalidTokenError:
            return False
    return False
