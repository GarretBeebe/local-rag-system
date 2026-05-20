"""Unit tests for JWT token creation and validation."""

import time
from datetime import UTC, datetime, timedelta

import jwt as pyjwt
import pytest

import web.auth as auth_module
from web.auth import create_token, is_valid_token

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


# --- Raw JWT encode/decode ---

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


# --- is_valid_token: API key path ---

def test_is_valid_token_correct_api_key(monkeypatch):
    monkeypatch.setattr(auth_module, "API_KEY", "my-secret-key")
    assert is_valid_token("my-secret-key") is True


def test_is_valid_token_wrong_api_key(monkeypatch):
    monkeypatch.setattr(auth_module, "API_KEY", "my-secret-key")
    assert is_valid_token("wrong-key") is False


def test_is_valid_token_empty_with_api_key(monkeypatch):
    monkeypatch.setattr(auth_module, "API_KEY", "my-secret-key")
    assert is_valid_token("") is False


# --- is_valid_token: JWT path ---

def test_is_valid_token_valid_jwt_known_user(monkeypatch):
    monkeypatch.setattr(auth_module, "API_KEY", "")
    monkeypatch.setattr(auth_module, "JWT_SECRET", SECRET)
    monkeypatch.setattr(
        auth_module.user_store, "get_hash", lambda u: "hash" if u == "alice" else None
    )
    assert is_valid_token(make_token(secret=SECRET, sub="alice")) is True


def test_is_valid_token_valid_jwt_unknown_user(monkeypatch):
    monkeypatch.setattr(auth_module, "API_KEY", "")
    monkeypatch.setattr(auth_module, "JWT_SECRET", SECRET)
    monkeypatch.setattr(auth_module.user_store, "get_hash", lambda u: None)
    assert is_valid_token(make_token(secret=SECRET, sub="alice")) is False


def test_is_valid_token_expired_jwt(monkeypatch):
    monkeypatch.setattr(auth_module, "API_KEY", "")
    monkeypatch.setattr(auth_module, "JWT_SECRET", SECRET)
    monkeypatch.setattr(auth_module.user_store, "get_hash", lambda u: "hash")
    assert is_valid_token(make_token(secret=SECRET, expired=True)) is False


def test_is_valid_token_tampered_jwt(monkeypatch):
    monkeypatch.setattr(auth_module, "API_KEY", "")
    monkeypatch.setattr(auth_module, "JWT_SECRET", SECRET)
    monkeypatch.setattr(auth_module.user_store, "get_hash", lambda u: "hash")
    token = make_token(secret=SECRET)
    parts = token.split(".")
    tampered = parts[0] + "." + "dGFtcGVyZWQ" + "." + parts[2]
    assert is_valid_token(tampered) is False


def test_is_valid_token_empty_with_jwt_secret(monkeypatch):
    monkeypatch.setattr(auth_module, "API_KEY", "")
    monkeypatch.setattr(auth_module, "JWT_SECRET", SECRET)
    monkeypatch.setattr(auth_module.user_store, "get_hash", lambda u: "hash")
    assert is_valid_token("") is False


def test_is_valid_token_no_auth_configured(monkeypatch):
    monkeypatch.setattr(auth_module, "API_KEY", "")
    monkeypatch.setattr(auth_module, "JWT_SECRET", "")
    assert is_valid_token("any-token") is False


# --- create_token ---

def test_create_token_sub_claim(monkeypatch):
    monkeypatch.setattr(auth_module, "JWT_SECRET", SECRET)
    monkeypatch.setattr(auth_module, "JWT_EXPIRY_HOURS", 8)
    token = create_token("alice")
    payload = decode_token(token, secret=SECRET)
    assert payload["sub"] == "alice"


def test_create_token_exp_in_future(monkeypatch):
    monkeypatch.setattr(auth_module, "JWT_SECRET", SECRET)
    monkeypatch.setattr(auth_module, "JWT_EXPIRY_HOURS", 8)
    token = create_token("alice")
    payload = decode_token(token, secret=SECRET)
    assert payload["exp"] > time.time()


def test_create_token_is_valid(monkeypatch):
    monkeypatch.setattr(auth_module, "API_KEY", "")
    monkeypatch.setattr(auth_module, "JWT_SECRET", SECRET)
    monkeypatch.setattr(auth_module, "JWT_EXPIRY_HOURS", 8)
    monkeypatch.setattr(
        auth_module.user_store, "get_hash", lambda u: "hash" if u == "alice" else None
    )
    token = create_token("alice")
    assert is_valid_token(token) is True
