"""
SQLite-backed user store for web UI authentication.

Stores bcrypt password hashes keyed by username in data/users.sqlite3,
which is persisted via the rag-data Docker volume.
"""

from pathlib import Path

from common.sqlite_store import SqliteStore

DB_PATH = Path(__file__).parent.parent / "data" / "users.sqlite3"

_store = SqliteStore(DB_PATH)


def init_db() -> None:
    conn = _store.conn
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY,
                password_hash TEXT NOT NULL,
                created_at REAL NOT NULL
            )
        """)


def get_hash(username: str) -> str | None:
    conn = _store.conn
    row = conn.execute(
        "SELECT password_hash FROM users WHERE username=?", (username,)
    ).fetchone()
    return row[0] if row else None


def upsert_user(username: str, password_hash: str) -> None:
    conn = _store.conn
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
    conn = _store.conn
    with conn:
        conn.execute("DELETE FROM users WHERE username=?", (username,))


def list_users() -> list[str]:
    conn = _store.conn
    rows = conn.execute("SELECT username FROM users ORDER BY username").fetchall()
    return [row[0] for row in rows]
