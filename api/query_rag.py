"""
RAG query interface: retrieves relevant chunks and generates an answer via Ollama.

ask(question) runs the full pipeline:
  1. Retrieve top-ranked chunks via retrieval.retrieve_best()
  2. Build a citation-aware prompt
  3. Send the prompt to the local LLM and print the response with source citations

ask_stream_sync(question) does the same but yields text chunks as they are generated,
suitable for streaming to clients.

Can be run directly as a script for interactive querying:
  python api/query_rag.py
"""

import logging
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import api.ollama_client as ollama_client
from api.retrieval import Chunk, RetrievalError, retrieve_best
from api.timing import timed as _timed
from common.types import RagMode
from settings import GEN_MODEL

logger = logging.getLogger(__name__)

_NO_CONTEXT_REPLY = "No relevant context found in the vector store yet."
_RETRIEVAL_UNAVAILABLE_STRICT = (
    "Retrieval service is unavailable. Cannot answer in strict mode."
)
_RETRIEVAL_UNAVAILABLE_NOTICE = (
    "\n\n---\n\n*Retrieval unavailable — answer from model knowledge only.*"
)


@dataclass(frozen=True)
class _DirectReply:
    text: str


@dataclass(frozen=True)
class _PromptQuery:
    prompt: str
    sources: str = ""


def _resolve_source(payload: dict[str, Any]) -> str:
    full = payload.get("filepath", payload.get("filename", "unknown"))
    return Path(full).name


def build_prompt(
    question: str,
    chunks: list[Chunk],
    rag_mode: RagMode = "augmented",
) -> str:
    context_blocks = []
    for i, chunk in enumerate(chunks, start=1):
        p = chunk.payload
        source = _resolve_source(p)
        chunk_ref = f"{p.get('chunk_index', '?')}/{p.get('chunk_total', '?')}"
        cite = f"[S{i}] {source} (chunk {chunk_ref})"
        context_blocks.append(f"{cite}\n{p.get('text', '')}")

    context = "\n\n---\n\n".join(context_blocks)

    if rag_mode == "augmented":
        instructions = (
            "Use the context below to inform your answer where relevant. "
            "You may supplement with your own knowledge where the context is incomplete. "
            "Cite sources as [S1], [S2] where context was used. Be concise."
        )
    else:
        instructions = (
            "Use ONLY the context below to answer. "
            "If the context is insufficient, say what is missing. "
            "Cite sources like [S1], [S2] in the answer. "
            "Do not invent details not present in the context. Be concise."
        )

    return f"""{instructions}

Context:
{context}

Question:
{question}
"""


def _format_sources(chunks: list[Chunk]) -> str:
    lines = []
    for i, chunk in enumerate(chunks, start=1):
        p = chunk.payload
        path = _resolve_source(p)
        score = chunk.rerank_score or 0.0
        lines.append(f"[S{i}] {path} (rerank={score:.4f})")
    joined = "\n".join(lines)
    return f"\n\n---\n\nSources:\n\n{joined}\n"


def _prepare_query(
    question: str,
    rag_mode: RagMode = "augmented",
) -> _PromptQuery | _DirectReply:
    try:
        chunks = retrieve_best(question)
    except RetrievalError:
        if rag_mode == "strict":
            return _DirectReply(_RETRIEVAL_UNAVAILABLE_STRICT)
        return _PromptQuery(question, sources=_RETRIEVAL_UNAVAILABLE_NOTICE)
    if not chunks:
        if rag_mode == "augmented":
            return _PromptQuery(question)
        return _DirectReply(_NO_CONTEXT_REPLY)

    return _PromptQuery(
        prompt=build_prompt(question, chunks, rag_mode),
        sources=_format_sources(chunks),
    )


def ask(question: str, model: str, rag_mode: RagMode = "augmented") -> str:
    prepared = _prepare_query(question, rag_mode)
    if isinstance(prepared, _DirectReply):
        return prepared.text

    with _timed("generate"):
        answer = ollama_client.generate(prepared.prompt, model).strip()

    return answer + prepared.sources


def ask_stream_sync(
    question: str,
    model: str,
    rag_mode: RagMode = "augmented",
    cancel: threading.Event | None = None,
) -> Iterator[str]:
    """Sync generator: retrieves context then streams generation chunks from Ollama."""
    if cancel and cancel.is_set():
        return

    prepared = _prepare_query(question, rag_mode)
    if isinstance(prepared, _DirectReply):
        yield prepared.text
        return

    with _timed("stream_generate"):
        yield from ollama_client.stream_generate(prepared.prompt, model, cancel=cancel)

    if cancel and cancel.is_set():
        return

    if prepared.sources:
        yield prepared.sources


if __name__ == "__main__":
    q = input("Ask a question: ").strip()
    print(ask(q, GEN_MODEL))
