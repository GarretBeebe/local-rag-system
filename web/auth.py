"""Session creation and validation for API key and opaque session bearer auth."""

import hmac

from settings import API_KEY, SESSION_EXPIRY_HOURS
from web import user_store


def create_session(username: str) -> str:
    """Create an opaque session token for the given username."""
    return user_store.create_session(username, SESSION_EXPIRY_HOURS)


def is_valid_token(token: str) -> bool:
    """Return True if token is a valid API key or active session token."""
    if not token:
        return False
    if API_KEY and hmac.compare_digest(token, API_KEY):
        return True
    return user_store.validate_session(token) is not None
