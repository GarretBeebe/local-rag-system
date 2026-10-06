"""Unit tests for index_documents embedding-failure behavior."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from common.qdrant import DENSE_VECTOR, SPARSE_VECTOR
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


def test_ensure_collection_indexes_filter_fields_on_creation(monkeypatch):
    import ingest.index_documents as index_documents

    client = MagicMock()
    client.collection_exists.return_value = False
    monkeypatch.setattr(index_documents, "get_qdrant_client", lambda: client)
    monkeypatch.setattr(index_documents, "_collection_ensured", False)

    index_documents.ensure_collection()

    created = client.create_collection.call_args.kwargs
    assert set(created["vectors_config"]) == {DENSE_VECTOR}
    assert set(created["sparse_vectors_config"]) == {SPARSE_VECTOR}
    indexed = {c.kwargs["field_name"] for c in client.create_payload_index.call_args_list}
    assert indexed == {"filepath", "filename"}


def test_ensure_collection_rejects_pre_bm25_layout(monkeypatch):
    import ingest.index_documents as index_documents

    client = MagicMock()
    client.collection_exists.return_value = True
    client.get_collection.return_value.config.params.sparse_vectors = None
    monkeypatch.setattr(index_documents, "get_qdrant_client", lambda: client)
    monkeypatch.setattr(index_documents, "_collection_ensured", False)

    with pytest.raises(RuntimeError, match="reset_collection"):
        index_documents.ensure_collection()


def test_embed_chunks_builds_dense_and_bm25_vectors(monkeypatch, tmp_path):
    from ingest.index_documents import _embed_chunks

    monkeypatch.setattr("ingest.index_documents.embed_batch", lambda chunks: [[0.1] * 768])
    path = tmp_path / "notes.md"
    path.write_text("content")

    (point,) = _embed_chunks(path, str(path), ["def retrieve_best(q):"], "doc-id")

    assert point.vector[DENSE_VECTOR] == [0.1] * 768
    assert point.vector[SPARSE_VECTOR].text == "notes md def retrieve_best q"
