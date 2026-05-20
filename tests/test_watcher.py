"""Unit tests for IndexWorker indexing policy via _index_if_changed."""

from pathlib import Path

import pytest


@pytest.fixture()
def existing_file(tmp_path: Path) -> Path:
    f = tmp_path / "doc.txt"
    f.write_text("hello")
    return f


def test_missing_path_returns_missing(tmp_path, monkeypatch):
    """Non-existent path returns MISSING without calling index_file."""
    index_calls = []
    monkeypatch.setattr("indexer.watcher.index_file", lambda p: index_calls.append(p) or "indexed")

    from indexer.watcher import IndexDecision, _index_if_changed

    result = _index_if_changed(str(tmp_path / "no_such_file.txt"))

    assert result == IndexDecision.MISSING
    assert index_calls == []


def test_unchanged_hash_skips_indexing(existing_file, monkeypatch):
    """Same hash as stored fingerprint returns UNCHANGED without calling index_file."""
    monkeypatch.setattr("indexer.watcher.sha256_file", lambda p: "abc123")
    monkeypatch.setattr("indexer.watcher.get_hash", lambda p: "abc123")
    index_calls = []
    monkeypatch.setattr("indexer.watcher.index_file", lambda p: index_calls.append(p) or "indexed")

    from indexer.watcher import IndexDecision, _index_if_changed

    result = _index_if_changed(str(existing_file))

    assert result == IndexDecision.UNCHANGED
    assert index_calls == []


def test_indexed_outcome_updates_fingerprint(existing_file, monkeypatch):
    """'indexed' outcome calls upsert_hash and returns INDEXED."""
    monkeypatch.setattr("indexer.watcher.sha256_file", lambda p: "new_hash")
    monkeypatch.setattr("indexer.watcher.get_hash", lambda p: "old_hash")
    monkeypatch.setattr("indexer.watcher.index_file", lambda p: "indexed")
    upserted = []
    monkeypatch.setattr("indexer.watcher.upsert_hash", lambda path, h: upserted.append((path, h)))

    from indexer.watcher import IndexDecision, _index_if_changed

    result = _index_if_changed(str(existing_file))

    assert result == IndexDecision.INDEXED
    assert upserted == [(str(existing_file), "new_hash")]


def test_skipped_outcome_does_not_update_fingerprint(existing_file, monkeypatch):
    """'skipped' outcome does not call upsert_hash and returns SKIPPED."""
    monkeypatch.setattr("indexer.watcher.sha256_file", lambda p: "new_hash")
    monkeypatch.setattr("indexer.watcher.get_hash", lambda p: "old_hash")
    monkeypatch.setattr("indexer.watcher.index_file", lambda p: "skipped")
    upserted = []
    monkeypatch.setattr("indexer.watcher.upsert_hash", lambda path, h: upserted.append((path, h)))

    from indexer.watcher import IndexDecision, _index_if_changed

    result = _index_if_changed(str(existing_file))

    assert result == IndexDecision.SKIPPED
    assert upserted == []


def test_failed_outcome_does_not_update_fingerprint(existing_file, monkeypatch):
    """'failed' outcome does not call upsert_hash and returns FAILED."""
    monkeypatch.setattr("indexer.watcher.sha256_file", lambda p: "new_hash")
    monkeypatch.setattr("indexer.watcher.get_hash", lambda p: "old_hash")
    monkeypatch.setattr("indexer.watcher.index_file", lambda p: "failed")
    upserted = []
    monkeypatch.setattr("indexer.watcher.upsert_hash", lambda path, h: upserted.append((path, h)))

    from indexer.watcher import IndexDecision, _index_if_changed

    result = _index_if_changed(str(existing_file))

    assert result == IndexDecision.FAILED
    assert upserted == []


def test_index_file_exception_is_logged_and_returns_failed(existing_file, monkeypatch, caplog):
    """Exception from index_file is logged and does not propagate; returns FAILED."""
    import logging

    monkeypatch.setattr("indexer.watcher.sha256_file", lambda p: "new_hash")
    monkeypatch.setattr("indexer.watcher.get_hash", lambda p: "old_hash")
    monkeypatch.setattr(
        "indexer.watcher.index_file",
        lambda p: (_ for _ in ()).throw(RuntimeError("index exploded")),
    )

    from indexer.watcher import IndexDecision, _index_if_changed

    with caplog.at_level(logging.ERROR):
        result = _index_if_changed(str(existing_file))

    assert result == IndexDecision.FAILED
    assert "index exploded" in caplog.text
