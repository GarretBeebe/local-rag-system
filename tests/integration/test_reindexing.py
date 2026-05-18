"""
Integration tests for re-indexing semantics.

These are the highest-priority regression tests for Phase 1. They require a
live Qdrant instance (automatically skipped if unavailable) and mock embed()
so they do not need Ollama.

Run: pytest -m integration
"""

from pathlib import Path

import pytest

from common.paths import normalize_path
from indexer.fingerprint_store import delete_hash, get_hash, upsert_hash
from ingest.index_documents import delete_document
from settings import COLLECTION, MAX_FILE_SIZE, get_qdrant_client

pytestmark = pytest.mark.integration


def _count_points(normalized_path: str) -> int:
    """Count Qdrant points whose filepath payload matches normalized_path."""
    from qdrant_client.models import FieldCondition, Filter, MatchValue
    result = get_qdrant_client().count(
        collection_name=COLLECTION,
        count_filter=Filter(
            must=[FieldCondition(key="filepath", match=MatchValue(value=normalized_path))]
        ),
        exact=True,
    )
    return result.count


@pytest.fixture(autouse=True)
def _ensure_collection():
    from ingest.index_documents import ensure_collection
    ensure_collection()


@pytest.fixture
def tmp_doc(tmp_path: Path, fake_embed) -> Path:
    """A temporary text file cleaned from Qdrant and fingerprint store on teardown."""
    f = tmp_path / "test_doc.txt"
    f.write_text("initial content for integration test")
    yield f
    delete_document(f)
    delete_hash(normalize_path(f))


def test_initial_index_produces_chunks(tmp_doc):
    from ingest.index_documents import index_file
    outcome = index_file(tmp_doc)
    assert outcome == "indexed"
    assert _count_points(normalize_path(tmp_doc)) >= 1


def test_reindex_leaves_exactly_one_version(tmp_doc, fake_embed):
    """Highest-priority regression: re-indexing a changed file must replace old
    chunks, not accumulate them. After two index calls the point count must equal
    what the second index produced alone — not first + second."""
    from ingest.index_documents import index_file

    # First index: short text → 1 chunk with the stub embed.
    tmp_doc.write_text("short initial version")
    index_file(tmp_doc)
    count_after_first = _count_points(normalize_path(tmp_doc))
    assert count_after_first >= 1

    # Second index: much longer text that produces more chunks.
    tmp_doc.write_text("word " * 400)
    index_file(tmp_doc)
    count_after_second = _count_points(normalize_path(tmp_doc))
    assert count_after_second >= 1

    # Third index: back to short. If stale chunks accumulated,
    # count would grow each round. After going back to short it
    # must match count_after_first exactly.
    tmp_doc.write_text("short final version")
    index_file(tmp_doc)
    count_after_third = _count_points(normalize_path(tmp_doc))

    assert count_after_third == count_after_first, (
        f"Stale chunks detected: expected {count_after_first} chunks "
        f"(same as first short index), got {count_after_third}"
    )


def test_failed_reindex_does_not_update_fingerprint(tmp_path, monkeypatch):
    """A failed re-index must not update the fingerprint hash."""
    from ingest.index_documents import index_file

    f = tmp_path / "will_fail.txt"
    f.write_text("some content")
    normalized = normalize_path(f)

    upsert_hash(normalized, "old-hash-value")

    def _fail(text):
        raise RuntimeError("forced embed failure")

    monkeypatch.setattr("ingest.index_documents.embed", _fail)

    outcome = index_file(f)
    assert outcome == "failed"
    assert get_hash(normalized) == "old-hash-value"

    delete_hash(normalized)
    delete_document(f)


def test_skipped_file_does_not_update_fingerprint(tmp_path):
    """Files skipped due to size must not update the fingerprint."""
    from ingest.index_documents import index_file

    f = tmp_path / "large_file.bin"
    f.write_bytes(b"x" * (MAX_FILE_SIZE + 1))
    normalized = normalize_path(f)
    prev_hash = get_hash(normalized)

    outcome = index_file(f)
    assert outcome == "skipped"
    assert get_hash(normalized) == prev_hash
