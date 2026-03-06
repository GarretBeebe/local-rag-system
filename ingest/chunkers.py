"""
Document chunking strategies for the ingest pipeline.

Splits raw document text into chunks suitable for embedding, dispatching
on file extension:
  - .py              — AST-based splitting at top-level function/class boundaries;
                       falls back to recursive character splitting if parsing fails
                       or the file contains no top-level definitions
  - .md / .markdown  — splits at Markdown header boundaries (H1–H6)
  - all others       — recursive character splitting with a 500-character window
                       and 100-character overlap (LangChain default)

Public API: chunk_document(path, text) -> list[str]
"""

import ast
import re
from pathlib import Path

from langchain_text_splitters import RecursiveCharacterTextSplitter


text_splitter = RecursiveCharacterTextSplitter(
    chunk_size=500,
    chunk_overlap=100,
)


def chunk_text(text: str) -> list[str]:
    return text_splitter.split_text(text)


# -------------------------
# Python code chunking
# -------------------------

# Segments larger than this are sub-split using chunk_text (see chunk_size above).
MAX_CHUNK_CHARS = 2000


def chunk_python(text: str) -> list[str]:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return chunk_text(text)

    chunks = []

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            segment = ast.get_source_segment(text, node)
            if not segment:
                continue
            if len(segment) > MAX_CHUNK_CHARS:
                chunks.extend(chunk_text(segment))
            else:
                chunks.append(segment)

    # fallback for scripts with no defs
    if not chunks:
        return chunk_text(text)

    return chunks


# -------------------------
# Markdown chunking
# -------------------------

HEADER_PATTERN = re.compile(r"^#{1,6} ")


def chunk_markdown(text: str) -> list[str]:
    lines = text.splitlines()
    chunks = []
    current = []

    for line in lines:
        if HEADER_PATTERN.match(line) and current:
            chunks.append("\n".join(current))
            current = []
        current.append(line)

    if current:
        chunks.append("\n".join(current))

    return [c for c in chunks if c.strip()]


# -------------------------
# Dispatcher
# -------------------------

def chunk_document(path: Path, text: str) -> list[str]:

    suffix = path.suffix.lower()

    if suffix == ".py":
        return chunk_python(text)

    if suffix in [".md", ".markdown"]:
        return chunk_markdown(text)

    return chunk_text(text)