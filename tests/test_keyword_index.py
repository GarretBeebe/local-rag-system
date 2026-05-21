"""Unit tests for BM25 refresh coordination."""

from api.keyword_index import KeywordIndex


def test_refresh_if_changed_skips_rebuild_when_version_unchanged(monkeypatch):
    index = KeywordIndex()
    index._last_seen_version = 5
    builds = []

    monkeypatch.setattr("api.keyword_index.get_index_version", lambda: 5)
    monkeypatch.setattr(index, "_build", lambda: builds.append(True))

    assert index._refresh_if_changed() is False
    assert builds == []


def test_refresh_if_changed_rebuilds_when_version_changes(monkeypatch):
    index = KeywordIndex()
    index._last_seen_version = 5
    builds = []

    monkeypatch.setattr("api.keyword_index.get_index_version", lambda: 6)
    monkeypatch.setattr(index, "_build", lambda: builds.append(True))

    assert index._refresh_if_changed() is True
    assert builds == [True]
    assert index._last_seen_version == 6
