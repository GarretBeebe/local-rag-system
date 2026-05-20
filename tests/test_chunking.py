"""Unit tests for document chunking logic."""

from pathlib import Path

from ingest.chunkers import chunk_document, chunk_markdown, chunk_python, chunk_text


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


def test_chunk_python_splits_on_top_level_defs():
    code = """\
def foo():
    return 1

def bar():
    return 2
"""
    chunks = chunk_python(code)
    assert len(chunks) == 2
    assert any("def foo" in c for c in chunks)
    assert any("def bar" in c for c in chunks)


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


def test_chunk_markdown_splits_at_headers():
    md = """\
# Section 1
Content of section 1.

# Section 2
Content of section 2.
"""
    chunks = chunk_markdown(md)
    assert len(chunks) == 2
    assert any("Section 1" in c for c in chunks)
    assert any("Section 2" in c for c in chunks)


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
    chunks = chunk_python(code)
    const_idx = next(i for i, c in enumerate(chunks) if "CONST" in c)
    foo_idx = next(i for i, c in enumerate(chunks) if "def foo" in c)
    second_idx = next(i for i, c in enumerate(chunks) if "SECOND" in c)
    assert const_idx < foo_idx < second_idx
