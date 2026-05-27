"""
Fixtures shared across integration tests.

Integration tests require a live Qdrant instance. They are skipped automatically
when Qdrant is unreachable. Run with:

    pytest -m integration
    pytest -m "not integration"   # unit tests only
"""

import pytest


@pytest.fixture(scope="session", autouse=True)
def require_qdrant():
    """Skip the entire integration suite if Qdrant is not reachable."""
    from common.qdrant import get_qdrant_client

    try:
        get_qdrant_client().get_collections()
    except Exception as e:
        pytest.skip(f"Qdrant not available: {e}")


@pytest.fixture
def fake_embed(monkeypatch):
    """Replace embed() with a deterministic stub so tests don't need Ollama."""
    monkeypatch.setattr(
        "ingest.index_documents.embed",
        lambda text: [0.1] * 768,
    )
