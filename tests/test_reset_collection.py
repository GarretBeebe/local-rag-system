"""Unit tests for reset_collection and fingerprint_store.clear_hashes()."""

import sys
from unittest.mock import MagicMock

import pytest

from common.sqlite_store import SqliteStore


@pytest.fixture()
def isolated_fp_store(monkeypatch, tmp_path):
    """Redirect fingerprint_store to an isolated temp DB for each test."""
    import indexer.fingerprint_store as fs
    store = SqliteStore(tmp_path / "test_fingerprints.sqlite3")
    monkeypatch.setattr(fs, "_store", store)
    fs.init_db()
    return fs


def test_clear_hashes_empties_store(isolated_fp_store):
    fs = isolated_fp_store
    fs.upsert_hash("/a/doc.txt", "hash1")
    fs.upsert_hash("/b/doc.txt", "hash2")
    assert len(fs.list_all_paths()) == 2

    fs.clear_hashes()
    assert fs.list_all_paths() == []


def test_clear_hashes_on_empty_store_is_safe(isolated_fp_store):
    fs = isolated_fp_store
    fs.clear_hashes()
    assert fs.list_all_paths() == []


@pytest.fixture()
def mock_qdrant(monkeypatch):
    client = MagicMock()
    client.collection_exists.return_value = True
    monkeypatch.setattr("ingest.reset_collection.get_qdrant_client", lambda: client)
    return client


def test_reset_deletes_collection_and_clears_fingerprints(monkeypatch, mock_qdrant):
    cleared = []
    monkeypatch.setattr("ingest.reset_collection.init_db", lambda: None)
    monkeypatch.setattr("ingest.reset_collection.clear_hashes", lambda: cleared.append(True))
    monkeypatch.setattr(sys, "argv", ["reset_collection"])

    from ingest.reset_collection import main
    main()

    mock_qdrant.delete_collection.assert_called_once()
    assert cleared == [True]


def test_vectors_only_skips_fingerprint_clear(monkeypatch, mock_qdrant):
    cleared = []
    monkeypatch.setattr("ingest.reset_collection.init_db", lambda: None)
    monkeypatch.setattr("ingest.reset_collection.clear_hashes", lambda: cleared.append(True))
    monkeypatch.setattr(sys, "argv", ["reset_collection", "--vectors-only"])

    from ingest.reset_collection import main
    main()

    mock_qdrant.delete_collection.assert_called_once()
    assert cleared == []


def test_reset_skips_delete_when_collection_missing(monkeypatch):
    client = MagicMock()
    client.collection_exists.return_value = False
    monkeypatch.setattr("ingest.reset_collection.get_qdrant_client", lambda: client)
    monkeypatch.setattr("ingest.reset_collection.init_db", lambda: None)
    monkeypatch.setattr("ingest.reset_collection.clear_hashes", lambda: None)
    monkeypatch.setattr(sys, "argv", ["reset_collection"])

    from ingest.reset_collection import main
    main()

    client.delete_collection.assert_not_called()
