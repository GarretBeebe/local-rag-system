"""Unit tests for index_documents embedding-failure behavior."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from common.types import IndexDecision


@pytest.fixture()
def tmp_doc(tmp_path: Path) -> Path:
    f = tmp_path / "doc.txt"
    f.write_text("chunk one content\nchunk two content\n")
    return f


def test_embed_failure_returns_failed(tmp_doc, monkeypatch):
    """Any embedding error must cause index_file to return 'failed'."""
    monkeypatch.setattr("ingest.index_documents.ensure_collection", lambda: None)
    monkeypatch.setattr(
        "ingest.index_documents.embed_batch",
        lambda chunks: (_ for _ in ()).throw(RuntimeError("embed down")),
    )

    upsert_calls = []
    mock_client = MagicMock()
    mock_client.upsert.side_effect = lambda **kw: upsert_calls.append(kw)
    monkeypatch.setattr("ingest.index_documents.get_qdrant_client", lambda: mock_client)

    from ingest.index_documents import index_file

    result = index_file(tmp_doc)

    assert result == IndexDecision.FAILED
    assert upsert_calls == [], "upsert must not be called when embedding fails"


def test_embed_failure_does_not_update_fingerprint(tmp_doc, monkeypatch):
    """index_file must return 'failed' (not 'indexed') so the watcher skips the fingerprint
    update."""
    monkeypatch.setattr("ingest.index_documents.ensure_collection", lambda: None)
    monkeypatch.setattr(
        "ingest.index_documents.embed_batch",
        lambda chunks: (_ for _ in ()).throw(RuntimeError("embed down")),
    )
    monkeypatch.setattr("ingest.index_documents.get_qdrant_client", MagicMock)

    from ingest.index_documents import index_file

    assert index_file(tmp_doc) == IndexDecision.FAILED


def test_embed_chunks_uses_one_batch_call(monkeypatch, tmp_path):
    from ingest.index_documents import _embed_chunks

    calls = []

    def fake_embed_batch(chunks):
        calls.append(chunks)
        return [[0.1] * 768 for _ in chunks]

    monkeypatch.setattr("ingest.index_documents.embed_batch", fake_embed_batch)

    path = tmp_path / "doc.txt"
    path.write_text("content")
    points = _embed_chunks(path, str(path), ["one", "two", "three"], "doc-id")

    assert calls == [["one", "two", "three"]]
    assert len(points) == 3
    assert {p.payload["chunk_total"] for p in points} == {3}


def test_embed_chunks_rejects_embedding_count_mismatch(monkeypatch, tmp_path):
    from ingest.index_documents import _embed_chunks

    monkeypatch.setattr("ingest.index_documents.embed_batch", lambda chunks: [[0.1] * 768])

    path = tmp_path / "doc.txt"
    path.write_text("content")
    with pytest.raises(RuntimeError, match="Expected 2 embeddings"):
        _embed_chunks(path, str(path), ["one", "two"], "doc-id")
