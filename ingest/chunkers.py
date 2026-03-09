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

from settings import MAX_CHUNK_CHARS, MAX_MD_CHUNK


text_splitter = RecursiveCharacterTextSplitter(
    chunk_size=500,
    chunk_overlap=100,
)


def chunk_text(text: str) -> list[str]:
    return text_splitter.split_text(text)


# -------------------------
# Python code chunking
# -------------------------

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
    sections = []
    current = []

    # First pass: split by markdown headers
    for line in lines:
        if HEADER_PATTERN.match(line) and current:
            sections.append("\n".join(current))
            current = []
        current.append(line)

    if current:
        sections.append("\n".join(current))

    sections = [s.strip() for s in sections if s.strip()]

    # Second pass: split oversized sections
    final_chunks = []

    for section in sections:
        if len(section) <= MAX_MD_CHUNK:
            final_chunks.append(section)
            continue

        # fallback: split large sections into paragraphs
        paragraphs = section.split("\n\n")
        buf = []

        for p in paragraphs:
            if buf and sum(len(x) for x in buf) + len(p) > MAX_MD_CHUNK:
                final_chunks.append("\n\n".join(buf))
                buf = []

            if len(p) > MAX_MD_CHUNK:
                final_chunks.extend(chunk_text(p))
            else:
                buf.append(p)

        if buf:
            final_chunks.append("\n\n".join(buf))

    return final_chunks


# -------------------------
# Dispatcher
# -------------------------

def chunk_document(path: Path, text: str) -> list[str]:

    suffix = path.suffix.lower()

    if suffix == ".py":
        return chunk_python(text)

    if suffix in {".md", ".markdown"}:
        return chunk_markdown(text)

    return chunk_text(text)