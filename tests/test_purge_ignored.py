"""Unit tests for purge_ignored fail-closed behavior."""

import pytest

from ingest.purge_ignored import NoAccessibleWatchRootsError, find_ignored_paths, purge_ignored


def _cfg(watch_paths=()):
    return {"watch_paths": [{"path": str(p)} for p in watch_paths]}


def test_inaccessible_watch_roots_raises(monkeypatch, tmp_path):
    """All configured roots missing → NoAccessibleWatchRootsError."""
    monkeypatch.setattr("ingest.purge_ignored.list_all_paths", lambda: ["/some/file.txt"])
    config = _cfg(watch_paths=[tmp_path / "does-not-exist"])
    with pytest.raises(NoAccessibleWatchRootsError, match="accessible"):
        find_ignored_paths(config)


def test_no_watch_paths_configured_raises(monkeypatch):
    """Empty watch_paths must raise so purge cannot evaluate all tracked paths."""
    monkeypatch.setattr("ingest.purge_ignored.list_all_paths", lambda: ["/tracked.txt"])
    with pytest.raises(NoAccessibleWatchRootsError, match="No watch_paths configured"):
        find_ignored_paths({})


def test_inaccessible_roots_prevents_deletion(monkeypatch, tmp_path):
    """`purge_ignored(apply=True)` must not call delete when roots are inaccessible."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text("watch_paths:\n  - path: /nonexistent/mount\n")

    monkeypatch.setattr("ingest.purge_ignored.init_db", lambda: None)
    monkeypatch.setattr("ingest.purge_ignored.list_all_paths", lambda: ["/some/file.txt"])

    deleted = []
    monkeypatch.setattr("ingest.purge_ignored.remove_indexed_document", lambda p: deleted.append(p))

    with pytest.raises(NoAccessibleWatchRootsError):
        purge_ignored(config_path, apply=True)

    assert deleted == []


def test_dry_run_also_raises_with_inaccessible_roots(monkeypatch, tmp_path):
    """Dry run must also fail closed — not silently return an empty list."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text("watch_paths:\n  - path: /nonexistent/mount\n")

    monkeypatch.setattr("ingest.purge_ignored.init_db", lambda: None)
    monkeypatch.setattr("ingest.purge_ignored.list_all_paths", lambda: ["/some/file.txt"])

    with pytest.raises(NoAccessibleWatchRootsError):
        purge_ignored(config_path, apply=False)


def test_accessible_root_only_evaluates_files_under_it(monkeypatch, tmp_path):
    """Files outside the accessible root must not be evaluated."""
    root = tmp_path / "docs"
    root.mkdir()
    inside = str(root / "note.ignored_ext")
    outside = "/completely/outside/path.txt"

    monkeypatch.setattr("ingest.purge_ignored.list_all_paths", lambda: [outside, inside])
    monkeypatch.setattr("ingest.purge_ignored.is_indexable_path", lambda *a, **kw: False)

    result = find_ignored_paths(_cfg(watch_paths=[root]))

    assert outside not in result
    assert inside in result


def test_apply_bumps_index_version_once_when_paths_removed(monkeypatch, tmp_path):
    root = tmp_path / "docs"
    root.mkdir()
    ignored = str(root / "note.ignored_ext")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(f"watch_paths:\n  - path: {root}\n")

    deleted = []
    bumps = []
    monkeypatch.setattr("ingest.purge_ignored.init_db", lambda: None)
    monkeypatch.setattr("ingest.purge_ignored.list_all_paths", lambda: [ignored])
    monkeypatch.setattr("ingest.purge_ignored.is_indexable_path", lambda *a, **kw: False)
    monkeypatch.setattr("ingest.purge_ignored.remove_indexed_document", lambda p: deleted.append(p))
    monkeypatch.setattr("ingest.purge_ignored.bump_index_version", lambda: bumps.append(True))

    assert purge_ignored(config_path, apply=True) == 1
    assert deleted == [ignored]
    assert bumps == [True]


def test_dry_run_does_not_bump_index_version(monkeypatch, tmp_path):
    root = tmp_path / "docs"
    root.mkdir()
    ignored = str(root / "note.ignored_ext")
    config_path = tmp_path / "config.yaml"
    config_path.write_text(f"watch_paths:\n  - path: {root}\n")

    bumps = []
    monkeypatch.setattr("ingest.purge_ignored.init_db", lambda: None)
    monkeypatch.setattr("ingest.purge_ignored.list_all_paths", lambda: [ignored])
    monkeypatch.setattr("ingest.purge_ignored.is_indexable_path", lambda *a, **kw: False)
    monkeypatch.setattr("ingest.purge_ignored.bump_index_version", lambda: bumps.append(True))

    assert purge_ignored(config_path, apply=False) == 1
    assert bumps == []
