"""
SQLite-backed user store for web UI authentication.

Stores bcrypt password hashes keyed by username in data/users.sqlite3,
which is persisted via the rag-data Docker volume.
"""

import sqlite3
import threading
from contextlib import suppress
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "users.sqlite3"

_local = threading.local()


def _get_conn() -> sqlite3.Connection:
    if not hasattr(_local, "conn"):
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(DB_PATH))
        with suppress(sqlite3.OperationalError):
            conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        _local.conn = conn
    return _local.conn


def init_db() -> None:
    conn = _get_conn()
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY,
                password_hash TEXT NOT NULL,
                created_at REAL NOT NULL
            )
        """)


def get_hash(username: str) -> str | None:
    conn = _get_conn()
    row = conn.execute(
        "SELECT password_hash FROM users WHERE username=?", (username,)
    ).fetchone()
    return row[0] if row else None


def upsert_user(username: str, password_hash: str) -> None:
    conn = _get_conn()
    with conn:
        conn.execute(
            """
            INSERT INTO users(username, password_hash, created_at)
            VALUES(?, ?, strftime('%s','now'))
            ON CONFLICT(username) DO UPDATE SET
                password_hash = excluded.password_hash,
                created_at = strftime('%s','now')
            """,
            (username, password_hash),
        )


def delete_user(username: str) -> None:
    conn = _get_conn()
    with conn:
        conn.execute("DELETE FROM users WHERE username=?", (username,))


def list_users() -> list[str]:
    conn = _get_conn()
    rows = conn.execute("SELECT username FROM users ORDER BY username").fetchall()
    return [row[0] for row in rows]
