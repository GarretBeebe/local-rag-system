"""Shared index-change marker used to coordinate API-side refreshes."""

from common.sqlite_store import SqliteStore
from settings import DATA_DIR

DB_PATH = DATA_DIR / "index_state.sqlite3"

_store = SqliteStore(DB_PATH)


def init_db() -> None:
    """Initialize the index state table and default version row."""
    conn = _store.conn
    with conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS index_state(
            name TEXT PRIMARY KEY,
            version INTEGER NOT NULL,
            updated_at REAL NOT NULL
        )
        """)
        conn.execute(
            """
            INSERT OR IGNORE INTO index_state(name, version, updated_at)
            VALUES('documents', 0, strftime('%s','now'))
            """
        )


def get_index_version() -> int:
    """Return the current document index version."""
    conn = _store.conn
    with conn:
        row = conn.execute(
            "SELECT version FROM index_state WHERE name='documents'"
        ).fetchone()
    return int(row[0]) if row else 0


def bump_index_version() -> int:
    """Increment and return the document index version."""
    conn = _store.conn
    with conn:
        conn.execute(
            """
            UPDATE index_state
            SET version = version + 1, updated_at = strftime('%s','now')
            WHERE name='documents'
            """
        )
        row = conn.execute(
            "SELECT version FROM index_state WHERE name='documents'"
        ).fetchone()
    return int(row[0]) if row else 0
