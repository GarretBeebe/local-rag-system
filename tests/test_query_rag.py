from api.query_rag import _format_sources, build_prompt
from api.retrieval import Chunk


def _chunk(filename: str, chunk_index: int = 0) -> Chunk:
    return Chunk(
        id=f"{filename}:{chunk_index}",
        payload={
            "filename": filename,
            "filepath": f"/private/path/{filename}",
            "chunk_index": chunk_index,
            "chunk_total": 3,
            "text": "retrieved text",
        },
        score=0.7,
        rerank_score=1.2345,
    )


def test_format_sources_lists_filenames_only():
    sources = _format_sources([_chunk("alpha.md"), _chunk("beta.md", 1)])

    assert sources == "\n\n---\n\nSources:\n\n[S1] alpha.md\n[S2] beta.md\n"
    assert "chunk" not in sources
    assert "rerank" not in sources
    assert "/private/path" not in sources


def test_build_prompt_tells_model_not_to_add_source_sections():
    chunks = [_chunk("alpha.md")]

    augmented = build_prompt("q", chunks, rag_mode="augmented")
    strict = build_prompt("q", chunks, rag_mode="strict")

    assert "Do not add a References or Sources section." in augmented
    assert "Do not add a References or Sources section." in strict
