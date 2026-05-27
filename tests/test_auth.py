"""Unit tests for opaque session creation and validation."""

import time

import pytest

import web.auth as auth_module
from web import user_store
from web.auth import create_session, is_valid_token

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    """Reinitialize user_store against a fresh temporary database."""
    from common.sqlite_store import SqliteStore

    monkeypatch.setattr(user_store, "_store", SqliteStore(tmp_path / "users.sqlite3"))
    user_store.init_db()


# ── user_store session methods ────────────────────────────────────────────────


def test_create_session_returns_64_char_hex(fresh_db):
    user_store.upsert_user("alice", "hash")
    token = user_store.create_session("alice", expiry_hours=8)
    assert len(token) == 64
    assert all(c in "0123456789abcdef" for c in token)


def test_create_session_tokens_are_unique(fresh_db):
    user_store.upsert_user("alice", "hash")
    t1 = user_store.create_session("alice", expiry_hours=8)
    t2 = user_store.create_session("alice", expiry_hours=8)
    assert t1 != t2


def test_validate_session_returns_username(fresh_db):
    user_store.upsert_user("alice", "hash")
    token = user_store.create_session("alice", expiry_hours=8)
    assert user_store.validate_session(token) == "alice"


def test_validate_session_returns_none_for_unknown_token(fresh_db):
    assert user_store.validate_session("no-such-token") is None


def test_validate_session_returns_none_for_expired_session(fresh_db):
    user_store.upsert_user("alice", "hash")
    # Insert a pre-expired session directly to avoid sleeping.
    conn = user_store._store.conn
    with conn:
        conn.execute(
            "INSERT INTO sessions(token, username, expires_at) VALUES(?, ?, ?)",
            ("expired-token", "alice", time.time() - 3600),
        )
    assert user_store.validate_session("expired-token") is None


def test_delete_session_revokes_immediately(fresh_db):
    user_store.upsert_user("alice", "hash")
    token = user_store.create_session("alice", expiry_hours=8)
    user_store.delete_session(token)
    assert user_store.validate_session(token) is None


def test_delete_session_is_safe_for_unknown_token(fresh_db):
    user_store.delete_session("nonexistent")  # must not raise


def test_delete_user_revokes_existing_sessions(fresh_db):
    user_store.upsert_user("alice", "hash")
    token = user_store.create_session("alice", expiry_hours=8)

    user_store.delete_user("alice")

    assert user_store.validate_session(token) is None


def test_password_update_revokes_existing_sessions(fresh_db):
    user_store.upsert_user("alice", "old-hash")
    token = user_store.create_session("alice", expiry_hours=8)

    user_store.upsert_user("alice", "new-hash")

    assert user_store.validate_session(token) is None


def test_validate_session_rejects_session_for_missing_user(fresh_db):
    conn = user_store._store.conn
    with conn:
        conn.execute(
            "INSERT INTO sessions(token, username, expires_at) VALUES(?, ?, ?)",
            ("orphan-token", "missing", time.time() + 3600),
        )

    assert user_store.validate_session("orphan-token") is None


def test_purge_expired_sessions_removes_stale_rows(fresh_db):
    user_store.upsert_user("alice", "hash")
    conn = user_store._store.conn
    with conn:
        conn.execute(
            "INSERT INTO sessions(token, username, expires_at) VALUES(?, ?, ?)",
            ("stale-token", "alice", time.time() - 1),
        )
    user_store.purge_expired_sessions()
    assert user_store.validate_session("stale-token") is None


def test_purge_expired_sessions_preserves_valid_rows(fresh_db):
    user_store.upsert_user("alice", "hash")
    token = user_store.create_session("alice", expiry_hours=8)
    user_store.purge_expired_sessions()
    assert user_store.validate_session(token) == "alice"


# ── is_valid_token: API key path ──────────────────────────────────────────────


def test_is_valid_token_correct_api_key(monkeypatch):
    monkeypatch.setattr(auth_module, "API_KEY", "my-secret-key")
    assert is_valid_token("my-secret-key") is True


def test_is_valid_token_wrong_api_key(monkeypatch):
    monkeypatch.setattr(auth_module, "API_KEY", "my-secret-key")
    monkeypatch.setattr(auth_module.user_store, "validate_session", lambda t: None)
    assert is_valid_token("wrong-key") is False


def test_is_valid_token_empty_with_api_key(monkeypatch):
    monkeypatch.setattr(auth_module, "API_KEY", "my-secret-key")
    assert is_valid_token("") is False


# ── is_valid_token: session path ──────────────────────────────────────────────


def test_is_valid_token_valid_session(monkeypatch):
    monkeypatch.setattr(auth_module, "API_KEY", "")
    monkeypatch.setattr(
        auth_module.user_store, "validate_session", lambda t: "alice" if t == "good-token" else None
    )
    assert is_valid_token("good-token") is True


def test_is_valid_token_expired_session(monkeypatch):
    monkeypatch.setattr(auth_module, "API_KEY", "")
    monkeypatch.setattr(auth_module.user_store, "validate_session", lambda t: None)
    assert is_valid_token("expired-token") is False


def test_is_valid_token_unknown_session(monkeypatch):
    monkeypatch.setattr(auth_module, "API_KEY", "")
    monkeypatch.setattr(auth_module.user_store, "validate_session", lambda t: None)
    assert is_valid_token("unknown-token") is False


def test_is_valid_token_empty_token(monkeypatch):
    monkeypatch.setattr(auth_module, "API_KEY", "")
    # validate_session should never be called for an empty token.
    monkeypatch.setattr(auth_module.user_store, "validate_session", lambda t: "alice")
    assert is_valid_token("") is False


# ── create_session ────────────────────────────────────────────────────────────


def test_create_session_delegates_username_and_expiry(monkeypatch):
    captured = {}

    def fake_create(username, expiry_hours):
        captured["username"] = username
        captured["expiry_hours"] = expiry_hours
        return "a" * 64

    monkeypatch.setattr(auth_module.user_store, "create_session", fake_create)
    monkeypatch.setattr(auth_module, "SESSION_EXPIRY_HOURS", 8)
    token = create_session("alice")
    assert token == "a" * 64
    assert captured == {"username": "alice", "expiry_hours": 8}
