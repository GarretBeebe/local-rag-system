"""Unit tests for IndexWorker indexing policy via _index_if_changed."""

import logging
from pathlib import Path

import pytest

from common.types import IndexDecision


@pytest.fixture()
def existing_file(tmp_path: Path) -> Path:
    f = tmp_path / "doc.txt"
    f.write_text("hello")
    return f


@pytest.fixture()
def changed_hashes(existing_file: Path, monkeypatch) -> Path:
    """Arrange sha256 and stored hash to differ so _index_if_changed proceeds to indexing."""
    monkeypatch.setattr("indexer.watcher.sha256_file", lambda p: "new_hash")
    monkeypatch.setattr("indexer.watcher.get_hash", lambda p: "old_hash")
    return existing_file


def test_missing_path_returns_missing(tmp_path: Path, monkeypatch) -> None:
    """Non-existent path returns MISSING without calling index_file."""
    index_calls: list[Path] = []
    monkeypatch.setattr(
        "indexer.watcher.index_file",
        lambda p: index_calls.append(p) or IndexDecision.INDEXED,
    )

    from indexer.watcher import _index_if_changed

    result = _index_if_changed(str(tmp_path / "no_such_file.txt"))

    assert result == IndexDecision.MISSING
    assert index_calls == []


def test_unchanged_hash_skips_indexing(existing_file: Path, monkeypatch) -> None:
    """Same hash as stored fingerprint returns UNCHANGED without calling index_file."""
    monkeypatch.setattr("indexer.watcher.sha256_file", lambda p: "abc123")
    monkeypatch.setattr("indexer.watcher.get_hash", lambda p: "abc123")
    index_calls: list[Path] = []
    monkeypatch.setattr(
        "indexer.watcher.index_file",
        lambda p: index_calls.append(p) or IndexDecision.INDEXED,
    )

    from indexer.watcher import _index_if_changed

    result = _index_if_changed(str(existing_file))

    assert result == IndexDecision.UNCHANGED
    assert index_calls == []


def test_indexed_outcome_updates_fingerprint(changed_hashes: Path, monkeypatch) -> None:
    """'indexed' outcome calls upsert_hash, bumps index version, and returns INDEXED."""
    monkeypatch.setattr("indexer.watcher.index_file", lambda p: IndexDecision.INDEXED)
    upserted: list[tuple[str, str]] = []
    bumps = []
    monkeypatch.setattr("indexer.watcher.upsert_hash", lambda p, h: upserted.append((p, h)))
    monkeypatch.setattr("indexer.watcher.bump_index_version", lambda: bumps.append(True))

    from indexer.watcher import _index_if_changed

    result = _index_if_changed(str(changed_hashes))

    assert result == IndexDecision.INDEXED
    assert upserted == [(str(changed_hashes), "new_hash")]
    assert bumps == [True]


def test_skipped_outcome_does_not_update_fingerprint(changed_hashes: Path, monkeypatch) -> None:
    """'skipped' outcome does not call upsert_hash and returns SKIPPED."""
    monkeypatch.setattr("indexer.watcher.index_file", lambda p: IndexDecision.SKIPPED)
    upserted: list[tuple[str, str]] = []
    bumps = []
    monkeypatch.setattr("indexer.watcher.upsert_hash", lambda p, h: upserted.append((p, h)))
    monkeypatch.setattr("indexer.watcher.bump_index_version", lambda: bumps.append(True))

    from indexer.watcher import _index_if_changed

    result = _index_if_changed(str(changed_hashes))

    assert result == IndexDecision.SKIPPED
    assert upserted == []
    assert bumps == []


def test_failed_outcome_does_not_update_fingerprint(changed_hashes: Path, monkeypatch) -> None:
    """'failed' outcome does not call upsert_hash and returns FAILED."""
    monkeypatch.setattr("indexer.watcher.index_file", lambda p: IndexDecision.FAILED)
    upserted: list[tuple[str, str]] = []
    bumps = []
    monkeypatch.setattr("indexer.watcher.upsert_hash", lambda p, h: upserted.append((p, h)))
    monkeypatch.setattr("indexer.watcher.bump_index_version", lambda: bumps.append(True))

    from indexer.watcher import _index_if_changed

    result = _index_if_changed(str(changed_hashes))

    assert result == IndexDecision.FAILED
    assert upserted == []
    assert bumps == []


def test_index_file_exception_is_logged_and_returns_failed(
    changed_hashes: Path, monkeypatch, caplog
) -> None:
    """Exception from index_file is logged and does not propagate; returns FAILED."""
    monkeypatch.setattr(
        "indexer.watcher.index_file",
        lambda p: (_ for _ in ()).throw(RuntimeError("index exploded")),
    )

    from indexer.watcher import _index_if_changed

    with caplog.at_level(logging.ERROR):
        result = _index_if_changed(str(changed_hashes))

    assert result == IndexDecision.FAILED
    assert "index exploded" in caplog.text
