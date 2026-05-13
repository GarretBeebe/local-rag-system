"""Shared SQLite connection helper for thread-local stores."""

import sqlite3
import threading
from contextlib import suppress
from pathlib import Path


def get_thread_local_connection(db_path: Path, local_state: threading.local) -> sqlite3.Connection:
    """Return a thread-local SQLite connection configured for lightweight local stores."""
    if not hasattr(local_state, "conn"):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path))
        with suppress(sqlite3.OperationalError):
            conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        local_state.conn = conn
    return local_state.conn


class SqliteStore:
    """Owns a thread-local SQLite connection for a single database file."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._local = threading.local()

    @property
    def conn(self) -> sqlite3.Connection:
        return get_thread_local_connection(self._db_path, self._local)
