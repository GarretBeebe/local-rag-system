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


def test_search_ignores_queries_below_min_token_count(monkeypatch):
    index = KeywordIndex()
    index._ids = ["a"]
    index._bm25 = object()

    monkeypatch.setattr("api.keyword_index.KEYWORD_MIN_QUERY_TOKENS", 2)

    assert index.search("one") == []


def test_build_disables_bm25_when_doc_limit_exceeded(monkeypatch):
    import importlib

    import api.keyword_index as keyword_index

    keyword_index = importlib.reload(keyword_index)
    real_keyword_index = keyword_index.KeywordIndex

    class Point:
        def __init__(self, point_id, payload):
            self.id = point_id
            self.payload = payload

    class Client:
        def collection_exists(self, collection_name):
            return True

        def scroll(self, **kwargs):
            return (
                [
                    Point("1", {"filename": "one.txt", "text": "alpha"}),
                    Point("2", {"filename": "two.txt", "text": "beta"}),
                ],
                None,
            )

    index = real_keyword_index()
    monkeypatch.setattr(keyword_index, "get_qdrant_client", lambda: Client())
    monkeypatch.setattr(keyword_index, "KEYWORD_INDEX_MAX_DOCS", 1)

    index._build()

    assert index._bm25 is None
    assert index._docs == []
    assert index._ids == []
    assert index.doc_count == 2
    assert index.known_filenames == {"one.txt", "two.txt"}
    assert "exceeds KEYWORD_INDEX_MAX_DOCS" in index.disabled_reason
