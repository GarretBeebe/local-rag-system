"""
Per-file SHA-256 fingerprint store backed by SQLite.

Tracks which files have been indexed and their content hashes so the watcher
can skip files that haven't changed since last indexing.
"""

import sqlite3
import threading
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "fingerprints.sqlite3"

_local = threading.local()


def _normalize(filepath: str) -> str:
    """Return a normalized absolute path for consistent storage in the database."""
    return str(Path(filepath).resolve())


def _get_conn() -> sqlite3.Connection:
    """Get a thread-local SQLite connection, initializing it on first use."""
    if not hasattr(_local, "conn"):
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError:
            pass
        conn.execute("PRAGMA synchronous=NORMAL")
        _local.conn = conn

    return _local.conn


def init_db() -> None:
    """Initialize the fingerprints table if it does not already exist."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = _get_conn()
    with conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS fingerprints(
            filepath TEXT PRIMARY KEY,
            sha256 TEXT NOT NULL,
            updated_at REAL NOT NULL
        )
        """)


def get_hash(filepath: str) -> str | None:
    """Return the stored SHA-256 hash for filepath, or None if unknown."""
    conn = _get_conn()
    with conn:
        row = conn.execute(
            "SELECT sha256 FROM fingerprints WHERE filepath=?",
            (_normalize(filepath),),
        ).fetchone()
    return row[0] if row else None


def upsert_hash(filepath: str, sha256: str) -> None:
    """Insert or update the SHA-256 hash for filepath."""
    conn = _get_conn()
    with conn:
        conn.execute(
            """
            INSERT INTO fingerprints(filepath, sha256, updated_at)
            VALUES(?,?,strftime('%s','now'))
            ON CONFLICT(filepath)
            DO UPDATE SET sha256=excluded.sha256, updated_at=strftime('%s','now')
            """,
            (_normalize(filepath), sha256),
        )


def delete_hash(filepath: str) -> None:
    """Delete any stored hash entry for filepath."""
    conn = _get_conn()
    with conn:
        conn.execute(
            "DELETE FROM fingerprints WHERE filepath=?",
            (_normalize(filepath),),
        )


def list_all_paths() -> list[str]:
    """Return all filepaths currently tracked in the fingerprint store."""
    conn = _get_conn()
    with conn:
        rows = conn.execute("SELECT filepath FROM fingerprints").fetchall()
    return [row[0] for row in rows]
