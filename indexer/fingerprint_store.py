import sqlite3
import threading
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "fingerprints.sqlite3"

_local = threading.local()


def _get_conn() -> sqlite3.Connection:
    if not hasattr(_local, "conn"):
        _local.conn = sqlite3.connect(DB_PATH)
    return _local.conn


def init_db() -> None:
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
    conn = _get_conn()
    with conn:
        row = conn.execute(
            "SELECT sha256 FROM fingerprints WHERE filepath=?",
            (filepath,),
        ).fetchone()
    return row[0] if row else None


def upsert_hash(filepath: str, sha256: str) -> None:
    conn = _get_conn()
    with conn:
        conn.execute(
            """
            INSERT INTO fingerprints(filepath, sha256, updated_at)
            VALUES(?,?,strftime('%s','now'))
            ON CONFLICT(filepath)
            DO UPDATE SET sha256=excluded.sha256, updated_at=strftime('%s','now')
            """,
            (filepath, sha256),
        )


def delete_hash(filepath: str) -> None:
    conn = _get_conn()
    with conn:
        conn.execute(
            "DELETE FROM fingerprints WHERE filepath=?",
            (filepath,),
        )
