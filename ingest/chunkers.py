"""
Document chunking strategies for the ingest pipeline.

Splits raw document text into chunks suitable for embedding, dispatching
on file extension:
  - .py              — AST-based splitting at top-level function/class boundaries;
                       falls back to recursive character splitting if parsing fails
                       or the file contains no top-level definitions
  - .md / .markdown  — splits at Markdown header boundaries (H1–H6)
  - all others       — recursive character splitting with a 500-character window
                       and 100-character overlap

Public API: chunk_document(path, text) -> list[str]
"""

import ast
import re
from pathlib import Path

from settings import CHUNK_OVERLAP, CHUNK_SIZE, MAX_CHUNK_CHARS, MAX_MD_CHUNK

_SEPARATORS = ["\n\n", "\n", " ", ""]


def _merge_splits(splits: list[str], separator: str) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    sep_len = len(separator)

    for split in splits:
        split_len = len(split)
        added_len = split_len + (sep_len if current else 0)
        if current and current_len + added_len > CHUNK_SIZE:
            chunks.append(separator.join(current))
            while current and current_len > CHUNK_OVERLAP:
                dropped = len(current[0]) + (sep_len if len(current) > 1 else 0)
                current_len -= dropped
                current.pop(0)
        current.append(split)
        current_len += split_len + (sep_len if len(current) > 1 else 0)

    if current:
        chunks.append(separator.join(current))

    return chunks


def _recursive_split(text: str, separators: list[str]) -> list[str]:
    separator = separators[-1]
    remaining: list[str] = []
    for i, sep in enumerate(separators):
        if sep == "" or sep in text:
            separator = sep
            remaining = separators[i + 1:]
            break

    parts = [s for s in text.split(separator) if s] if separator else list(text)
    good: list[str] = []
    result: list[str] = []

    for part in parts:
        if len(part) > CHUNK_SIZE:
            if good:
                result.extend(_merge_splits(good, separator))
                good = []
            result.extend(_recursive_split(part, remaining) if remaining else [part])
        else:
            good.append(part)

    if good:
        result.extend(_merge_splits(good, separator))

    return result


def chunk_text(text: str) -> list[str]:
    if not text.strip():
        return []
    return _recursive_split(text, _SEPARATORS)


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


def _split_markdown_sections(text: str) -> list[str]:
    """Split a markdown document into sections at header boundaries."""
    lines = text.splitlines()
    sections: list[str] = []
    current: list[str] = []

    for line in lines:
        if HEADER_PATTERN.match(line) and current:
            sections.append("\n".join(current))
            current = []
        current.append(line)

    if current:
        sections.append("\n".join(current))

    return [s.strip() for s in sections if s.strip()]


def _split_oversized_markdown_section(section: str) -> list[str]:
    """Split a large markdown section into smaller chunks respecting MAX_MD_CHUNK."""
    if len(section) <= MAX_MD_CHUNK:
        return [section]

    paragraphs = section.split("\n\n")
    final_chunks: list[str] = []
    buf: list[str] = []
    buf_len = 0  # length of "\n\n".join(buf)

    for p in paragraphs:
        # Adding a paragraph adds its length plus the separator ("\n\n") if buffer isn't empty.
        additional = len(p) + (2 if buf else 0)
        if buf and (buf_len + additional) > MAX_MD_CHUNK:
            final_chunks.append("\n\n".join(buf))
            buf = []
            buf_len = 0

        if len(p) > MAX_MD_CHUNK:
            final_chunks.extend(chunk_text(p))
        else:
            buf.append(p)
            buf_len += len(p) + (2 if buf_len else 0)

    if buf:
        final_chunks.append("\n\n".join(buf))

    return final_chunks


def chunk_markdown(text: str) -> list[str]:
    """Chunk markdown into sections and sub-sections that fit within MAX_MD_CHUNK."""
    sections = _split_markdown_sections(text)
    final_chunks: list[str] = []

    for section in sections:
        final_chunks.extend(_split_oversized_markdown_section(section))

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
