import sqlite3
from pathlib import Path

DB_PATH = Path("data/fingerprints.sqlite3")


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS fingerprints(
            filepath TEXT PRIMARY KEY,
            sha256 TEXT,
            updated_at REAL
        )
        """)
        conn.commit()


def get_hash(filepath: str):

    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT sha256 FROM fingerprints WHERE filepath=?",
            (filepath,),
        ).fetchone()

        return row[0] if row else None


def upsert_hash(filepath: str, sha256: str):

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO fingerprints(filepath, sha256, updated_at)
            VALUES(?,?,strftime('%s','now'))
            ON CONFLICT(filepath)
            DO UPDATE SET sha256=excluded.sha256
            """,
            (filepath, sha256),
        )

        conn.commit()


def delete_hash(filepath: str):

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "DELETE FROM fingerprints WHERE filepath=?",
            (filepath,),
        )

        conn.commit()