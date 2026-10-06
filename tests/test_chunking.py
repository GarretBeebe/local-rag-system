"""Unit tests for document chunking logic."""

from pathlib import Path

from ingest.chunkers import (
    _merge_splits,
    chunk_document,
    chunk_markdown,
    chunk_python,
    chunk_text,
)
from settings import CHUNK_SIZE


def test_chunk_text_splits_long_input():
    text = "word " * 500
    chunks = chunk_text(text)
    assert len(chunks) > 1
    assert all(isinstance(c, str) for c in chunks)


def test_chunk_text_short_input_returns_single_chunk():
    text = "short text"
    chunks = chunk_text(text)
    assert len(chunks) == 1
    assert chunks[0] == text


def test_chunk_text_empty_input():
    chunks = chunk_text("")
    assert chunks == []


def test_chunk_python_packs_small_defs_together():
    code = """\
def foo():
    return 1

def bar():
    return 2
"""
    chunks = chunk_python(code)
    assert len(chunks) == 1
    assert "def foo" in chunks[0] and "def bar" in chunks[0]


def test_chunk_python_keeps_large_defs_separate():
    body = "    x = 1\n" * (CHUNK_SIZE // 10)  # each function alone is about CHUNK_SIZE
    code = f"def foo():\n{body}\n\ndef bar():\n{body}"
    chunks = chunk_python(code)
    assert len(chunks) == 2
    assert chunks[0].startswith("def foo") and chunks[1].startswith("def bar")


def test_chunk_python_imports_share_one_chunk():
    code = "import os\nimport sys\nfrom pathlib import Path\n\nX = 1\n"
    assert len(chunk_python(code)) == 1


def test_chunk_python_keeps_decorators_with_their_function():
    code = 'import x\n\n@app.post("/v1/chat")\n@limit(3)\ndef chat():\n    return 1\n'
    chunks = chunk_python(code)
    assert any('@app.post("/v1/chat")\n@limit(3)\ndef chat():' in c for c in chunks)
    assert not any(c.strip().startswith("@") and "def chat" not in c for c in chunks)


def test_chunk_python_does_not_duplicate_semicolon_lines():
    chunks = chunk_python("import a; import b\nX = 1\n")
    assert "".join(chunks).count("import a; import b") == 1


def test_chunk_python_class_becomes_single_chunk():
    code = """\
class MyClass:
    def method(self):
        pass
"""
    chunks = chunk_python(code)
    assert len(chunks) == 1
    assert "class MyClass" in chunks[0]


def test_chunk_python_no_defs_falls_back_to_text_splitter():
    code = "x = 1\ny = 2\n"
    chunks = chunk_python(code)
    # falls back to recursive text splitter, returns at least one chunk
    assert len(chunks) >= 1


def test_chunk_python_invalid_syntax_falls_back():
    code = "def (broken syntax"
    chunks = chunk_python(code)
    assert len(chunks) >= 1


def test_chunk_markdown_packs_small_sections_together():
    md = """\
# Section 1
Content of section 1.

# Section 2
Content of section 2.
"""
    chunks = chunk_markdown(md)
    assert len(chunks) == 1
    assert "Section 1" in chunks[0] and "Section 2" in chunks[0]


def test_chunk_markdown_splits_large_sections_at_headers():
    body = "word " * (CHUNK_SIZE // 5)  # each section alone is about CHUNK_SIZE
    chunks = chunk_markdown(f"# Section 1\n{body}\n\n# Section 2\n{body}")
    assert len(chunks) == 2
    assert chunks[0].startswith("# Section 1") and chunks[1].startswith("# Section 2")


def test_chunk_markdown_ignores_headers_inside_code_fences():
    body = "word " * (CHUNK_SIZE // 5)  # large enough that real sections would not merge
    md = (
        f"# Setup\n{body}\n\n```bash\n# install deps\nuv sync\n```\n\n"
        f"~~~\n# also code\n~~~\n\n   ````python\n# indented fence\n```\n# still code\n````\n"
    )
    chunks = chunk_markdown(md)
    assert len(chunks) == 1
    assert "# install deps" in chunks[0] and "# still code" in chunks[0]


def test_chunk_markdown_closing_fence_must_have_no_info_string():
    body = "word " * (CHUNK_SIZE // 5)
    md = f"# Doc\n{body}\n\n```\ncode\n```js\n# not a header\n```\n"
    assert len(chunk_markdown(md)) == 1


def test_chunk_markdown_no_headers_returns_single_chunk():
    md = "plain paragraph with no headers"
    chunks = chunk_markdown(md)
    assert len(chunks) == 1


def test_chunk_markdown_empty_returns_empty():
    chunks = chunk_markdown("")
    assert chunks == []


def test_chunk_document_dispatches_py():
    code = "def f():\n    pass\n"
    chunks = chunk_document(Path("mod.py"), code)
    assert any("def f" in c for c in chunks)


def test_chunk_document_dispatches_md():
    md = "# H1\ncontent"
    chunks = chunk_document(Path("readme.md"), md)
    assert any("H1" in c for c in chunks)


def test_chunk_document_dispatches_txt():
    text = "plain text " * 100
    chunks = chunk_document(Path("notes.txt"), text)
    assert len(chunks) >= 1


def test_chunk_document_case_insensitive_extension():
    code = "def f():\n    pass\n"
    chunks = chunk_document(Path("mod.PY"), code)
    assert any("def f" in c for c in chunks)


# --- Module-level code preservation ---


def test_chunk_python_imports_only_are_present():
    code = "import os\nimport sys\n"
    chunks = chunk_python(code)
    assert chunks
    assert any("import os" in c or "import sys" in c for c in chunks)


def test_chunk_python_imports_before_function_both_present():
    code = "import os\n\ndef foo():\n    return os.getcwd()\n"
    chunks = chunk_python(code)
    assert any("import os" in c for c in chunks)
    assert any("def foo" in c for c in chunks)


def test_chunk_python_code_between_functions_is_present():
    code = "def foo():\n    pass\n\nX = 42\n\ndef bar():\n    pass\n"
    chunks = chunk_python(code)
    assert any("X = 42" in c for c in chunks)
    assert any("def foo" in c for c in chunks)
    assert any("def bar" in c for c in chunks)


def test_chunk_python_main_guard_is_present():
    code = "def run():\n    pass\n\nif __name__ == '__main__':\n    run()\n"
    chunks = chunk_python(code)
    assert any("__main__" in c for c in chunks)


def test_chunk_python_source_order_preserved():
    code = "CONST = 1\n\ndef foo():\n    pass\n\nSECOND = 2\n"
    joined = "\n".join(chunk_python(code))
    assert joined.index("CONST") < joined.index("def foo") < joined.index("SECOND")


# --- _merge_splits without overlap (structural chunkers) ---


def test_merge_splits_without_overlap_packs_and_never_repeats():
    pieces = ["a" * 200, "b" * 200, "c" * 200, "d" * (CHUNK_SIZE + 50), "e" * 10]
    merged = _merge_splits(pieces, "\n\n", overlap=0)
    assert merged == ["a" * 200 + "\n\n" + "b" * 200, "c" * 200, "d" * (CHUNK_SIZE + 50), "e" * 10]
