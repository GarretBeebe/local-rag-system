"""Unit tests for retrieval failure propagation and mode-aware handling."""

import pytest
from unittest.mock import MagicMock, patch

from api.retrieval import RetrievalUnavailable


def test_qdrant_recall_raises_retrieval_unavailable_on_qdrant_error(monkeypatch):
    """qdrant_recall must raise RetrievalUnavailable instead of swallowing the error."""
    from api.retrieval import qdrant_recall

    mock_client = MagicMock()
    mock_client.query_points.side_effect = RuntimeError("connection refused")
    monkeypatch.setattr("api.retrieval.get_qdrant_client", lambda: mock_client)

    with pytest.raises(RetrievalUnavailable):
        qdrant_recall([0.1] * 768)


def test_prepare_query_strict_mode_returns_unavailable_reply(monkeypatch):
    """In strict mode, a RetrievalUnavailable must produce a direct refusal reply."""
    from api.query_rag import _prepare_query

    monkeypatch.setattr(
        "api.query_rag.retrieve_best",
        lambda *a, **kw: (_ for _ in ()).throw(RetrievalUnavailable("qdrant down")),
    )

    result = _prepare_query("anything", rag_mode="strict")
    assert result.direct_reply is not None
    assert "unavailable" in result.direct_reply.lower()
    assert result.prompt is None


def test_prepare_query_augmented_mode_includes_degraded_notice(monkeypatch):
    """In augmented mode, a RetrievalUnavailable must allow fallback with a degradation notice."""
    from api.query_rag import _prepare_query

    monkeypatch.setattr(
        "api.query_rag.retrieve_best",
        lambda *a, **kw: (_ for _ in ()).throw(RetrievalUnavailable("qdrant down")),
    )

    result = _prepare_query("my question", rag_mode="augmented")
    assert result.direct_reply is None
    assert result.prompt == "my question"
    assert "unavailable" in result.sources.lower()


def test_prepare_query_empty_result_uses_no_context_path(monkeypatch):
    """A successful Qdrant search returning no chunks must not be confused with a failure."""
    from api.query_rag import _NO_CONTEXT_REPLY, _prepare_query

    monkeypatch.setattr("api.query_rag.retrieve_best", lambda *a, **kw: [])

    strict = _prepare_query("q", rag_mode="strict")
    assert strict.direct_reply == _NO_CONTEXT_REPLY

    augmented = _prepare_query("q", rag_mode="augmented")
    assert augmented.direct_reply is None
    assert augmented.prompt == "q"
    assert "unavailable" not in augmented.sources.lower()
