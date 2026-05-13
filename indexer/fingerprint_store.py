"""
Per-file SHA-256 fingerprint store backed by SQLite.

Tracks which files have been indexed and their content hashes so the watcher
can skip files that haven't changed since last indexing.
"""

import sqlite3
import threading
from pathlib import Path

from common.paths import normalize_path
from common.sqlite_store import get_thread_local_connection

DB_PATH = Path(__file__).parent.parent / "data" / "fingerprints.sqlite3"

_local = threading.local()


def _get_conn() -> sqlite3.Connection:
    """Get a thread-local SQLite connection, initializing it on first use."""
    return get_thread_local_connection(DB_PATH, _local)


def init_db() -> None:
    """Initialize the fingerprints table if it does not already exist."""
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
            (normalize_path(filepath),),
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
            (normalize_path(filepath), sha256),
        )


def delete_hash(filepath: str) -> None:
    """Delete any stored hash entry for filepath."""
    conn = _get_conn()
    with conn:
        conn.execute(
            "DELETE FROM fingerprints WHERE filepath=?",
            (normalize_path(filepath),),
        )


def list_all_paths() -> list[str]:
    """Return all filepaths currently tracked in the fingerprint store."""
    conn = _get_conn()
    with conn:
        rows = conn.execute("SELECT filepath FROM fingerprints").fetchall()
    return [row[0] for row in rows]
