"""Unit tests for retrieval failure propagation and mode-aware handling."""

from unittest.mock import MagicMock

import pytest

from api.retrieval import RetrievalError


def test_qdrant_recall_raises_retrieval_unavailable_on_qdrant_error(monkeypatch):
    """qdrant_recall must raise RetrievalError instead of swallowing the error."""
    from api.retrieval import qdrant_recall

    mock_client = MagicMock()
    mock_client.query_points.side_effect = RuntimeError("connection refused")
    monkeypatch.setattr("api.retrieval.get_qdrant_client", lambda: mock_client)

    with pytest.raises(RetrievalError):
        qdrant_recall([0.1] * 768)


def test_prepare_query_strict_mode_returns_unavailable_reply(monkeypatch):
    """In strict mode, a RetrievalError must produce a direct refusal reply."""
    from api.query_rag import _DirectReply, _prepare_query

    monkeypatch.setattr(
        "api.query_rag.retrieve_best",
        lambda *a, **kw: (_ for _ in ()).throw(RetrievalError("qdrant down")),
    )

    result = _prepare_query("anything", rag_mode="strict")
    assert isinstance(result, _DirectReply)
    assert "unavailable" in result.text.lower()


def test_prepare_query_augmented_mode_includes_degraded_notice(monkeypatch):
    """In augmented mode, a RetrievalError must allow fallback with a degradation notice."""
    from api.query_rag import _prepare_query, _PromptQuery

    monkeypatch.setattr(
        "api.query_rag.retrieve_best",
        lambda *a, **kw: (_ for _ in ()).throw(RetrievalError("qdrant down")),
    )

    result = _prepare_query("my question", rag_mode="augmented")
    assert isinstance(result, _PromptQuery)
    assert result.prompt == "my question"
    assert "unavailable" in result.sources.lower()


def test_prepare_query_empty_result_uses_no_context_path(monkeypatch):
    """A successful Qdrant search returning no chunks must not be confused with a failure."""
    from api.query_rag import _NO_CONTEXT_REPLY, _DirectReply, _prepare_query, _PromptQuery

    monkeypatch.setattr("api.query_rag.retrieve_best", lambda *a, **kw: [])

    strict = _prepare_query("q", rag_mode="strict")
    assert isinstance(strict, _DirectReply)
    assert strict.text == _NO_CONTEXT_REPLY

    augmented = _prepare_query("q", rag_mode="augmented")
    assert isinstance(augmented, _PromptQuery)
    assert augmented.prompt == "q"
    assert "unavailable" not in augmented.sources.lower()
