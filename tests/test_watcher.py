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


def test_skipped_outcome_removes_stale_vectors_when_previously_indexed(
    changed_hashes: Path, monkeypatch
) -> None:
    """Previously indexed file returning SKIPPED deletes stale vectors; fingerprint unchanged."""
    monkeypatch.setattr("indexer.watcher.index_file", lambda p: IndexDecision.SKIPPED)
    upserted: list[tuple[str, str]] = []
    bumps: list[bool] = []
    removed: list[str] = []
    monkeypatch.setattr("indexer.watcher.upsert_hash", lambda p, h: upserted.append((p, h)))
    monkeypatch.setattr("indexer.watcher.bump_index_version", lambda: bumps.append(True))
    monkeypatch.setattr("indexer.watcher.remove_indexed_document", lambda p: removed.append(p))

    from indexer.watcher import _index_if_changed

    result = _index_if_changed(str(changed_hashes))

    assert result == IndexDecision.SKIPPED
    assert upserted == []
    assert removed == [str(changed_hashes)]
    assert bumps == [True]


def test_skipped_outcome_never_indexed_does_not_remove_document(
    existing_file: Path, monkeypatch
) -> None:
    """File returning SKIPPED with no prior fingerprint does not call remove_indexed_document."""
    monkeypatch.setattr("indexer.watcher.sha256_file", lambda p: "some_hash")
    monkeypatch.setattr("indexer.watcher.get_hash", lambda p: None)  # never indexed
    monkeypatch.setattr("indexer.watcher.index_file", lambda p: IndexDecision.SKIPPED)
    removed: list[str] = []
    monkeypatch.setattr("indexer.watcher.remove_indexed_document", lambda p: removed.append(p))
    monkeypatch.setattr("indexer.watcher.upsert_hash", lambda p, h: None)
    monkeypatch.setattr("indexer.watcher.bump_index_version", lambda: None)

    from indexer.watcher import _index_if_changed

    result = _index_if_changed(str(existing_file))

    assert result == IndexDecision.SKIPPED
    assert removed == []


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


# --- _iter_schedulable_dirs ---

def test_iter_schedulable_dirs_no_exclusions_yields_all(tmp_path: Path) -> None:
    """With no exclude_dirs, all directories including nested ones are yielded."""
    (tmp_path / "a" / "b").mkdir(parents=True)

    from indexer.watcher import _iter_schedulable_dirs

    result = set(_iter_schedulable_dirs(tmp_path, []))
    assert result == {tmp_path, tmp_path / "a", tmp_path / "a" / "b"}


def test_iter_schedulable_dirs_excluded_dir_and_children_are_pruned(tmp_path: Path) -> None:
    """An excluded directory and all its descendants are absent from results."""
    (tmp_path / ".venv" / "lib").mkdir(parents=True)

    from indexer.watcher import _iter_schedulable_dirs

    result = set(_iter_schedulable_dirs(tmp_path, [".venv"]))
    assert tmp_path in result
    assert tmp_path / ".venv" not in result
    assert tmp_path / ".venv" / "lib" not in result


def test_iter_schedulable_dirs_non_excluded_sibling_is_kept(tmp_path: Path) -> None:
    """A sibling of an excluded directory is still yielded."""
    (tmp_path / ".venv").mkdir()
    (tmp_path / "src").mkdir()

    from indexer.watcher import _iter_schedulable_dirs

    result = set(_iter_schedulable_dirs(tmp_path, [".venv"]))
    assert tmp_path / "src" in result
    assert tmp_path / ".venv" not in result


def test_iter_schedulable_dirs_nested_exclusion_stops_recursion(tmp_path: Path) -> None:
    """Exclusion at a nested level stops traversal into that subtree only."""
    (tmp_path / "a" / "__pycache__" / "x").mkdir(parents=True)

    from indexer.watcher import _iter_schedulable_dirs

    result = set(_iter_schedulable_dirs(tmp_path, ["__pycache__"]))
    assert tmp_path in result
    assert tmp_path / "a" in result
    assert tmp_path / "a" / "__pycache__" not in result
    assert tmp_path / "a" / "__pycache__" / "x" not in result
