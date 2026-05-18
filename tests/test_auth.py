"""Unit tests for JWT token creation and validation."""

import time
from datetime import UTC, datetime, timedelta

import jwt as pyjwt
import pytest

SECRET = "test-secret-key-long-enough-for-hs256-minimum"
ALGORITHM = "HS256"


def make_token(
    secret: str = SECRET,
    sub: str = "alice",
    expiry_hours: int = 8,
    *,
    expired: bool = False,
) -> str:
    if expired:
        exp = datetime.now(UTC) - timedelta(hours=1)
    else:
        exp = datetime.now(UTC) + timedelta(hours=expiry_hours)
    payload = {"sub": sub, "exp": exp}
    return pyjwt.encode(payload, secret, algorithm=ALGORITHM)


def decode_token(token: str, secret: str = SECRET) -> dict:
    return pyjwt.decode(token, secret, algorithms=[ALGORITHM])


# --- Valid tokens ---

def test_valid_token_decodes_cleanly():
    token = make_token()
    payload = decode_token(token)
    assert payload["sub"] == "alice"


def test_token_contains_exp_claim():
    token = make_token()
    payload = decode_token(token)
    assert "exp" in payload


def test_token_expiry_is_in_future():
    token = make_token(expiry_hours=8)
    payload = decode_token(token)
    assert payload["exp"] > time.time()


def test_different_subjects_produce_different_tokens():
    t1 = make_token(sub="alice")
    t2 = make_token(sub="bob")
    assert t1 != t2


# --- Invalid tokens ---

def test_wrong_secret_raises():
    token = make_token(secret="correct-secret-long-enough-for-hs256-minimum")
    with pytest.raises(pyjwt.InvalidSignatureError):
        decode_token(token, secret="wrong-secret-long-enough-for-hs256-minimum")


def test_expired_token_raises():
    token = make_token(expired=True)
    with pytest.raises(pyjwt.ExpiredSignatureError):
        decode_token(token)


def test_malformed_token_raises():
    with pytest.raises(pyjwt.DecodeError):
        decode_token("not.a.valid.token")


def test_empty_token_raises():
    with pytest.raises(pyjwt.DecodeError):
        decode_token("")


def test_tampered_payload_raises():
    token = make_token()
    parts = token.split(".")
    tampered = parts[0] + "." + "dGFtcGVyZWQ" + "." + parts[2]
    with pytest.raises(pyjwt.InvalidSignatureError):
        decode_token(tampered)
