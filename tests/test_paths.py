"""Unit tests for path normalization and ignore-pattern matching."""

from pathlib import Path

from common.paths import (
    has_allowed_extension,
    is_indexable_path,
    matches_ignore_pattern,
    normalize_path,
)

# --- normalize_path ---

def test_normalize_path_string_input():
    result = normalize_path("/tmp/foo/bar.txt")
    assert result == str(Path("/tmp/foo/bar.txt").resolve())


def test_normalize_path_path_input():
    p = Path("/tmp/foo/bar.txt")
    assert normalize_path(p) == str(p.resolve())


def test_normalize_path_returns_string():
    assert isinstance(normalize_path("/tmp/x"), str)


# --- has_allowed_extension ---

def test_has_allowed_extension_match():
    assert has_allowed_extension("doc.md", {".md", ".txt"})


def test_has_allowed_extension_no_match():
    assert not has_allowed_extension("doc.pdf", {".md", ".txt"})


def test_has_allowed_extension_case_insensitive():
    assert has_allowed_extension("doc.MD", {".md"})
    assert has_allowed_extension("doc.md", {".MD"})


def test_has_allowed_extension_no_extension():
    assert not has_allowed_extension("Makefile", {".md", ".txt"})


# --- matches_ignore_pattern ---

def test_matches_ignore_pattern_exact_component():
    assert matches_ignore_pattern("/project/.git/config", [".git"])


def test_matches_ignore_pattern_glob_component():
    assert matches_ignore_pattern("/project/__pycache__/mod.cpython-311.pyc", ["__pycache__"])


def test_matches_ignore_pattern_no_match():
    assert not matches_ignore_pattern("/project/src/main.py", ["__pycache__", ".git"])


def test_matches_ignore_pattern_glob_star():
    assert matches_ignore_pattern("/project/build/output.txt", ["build/*"])


def test_matches_ignore_pattern_empty_patterns():
    assert not matches_ignore_pattern("/project/file.py", [])


# --- is_indexable_path ---

def test_is_indexable_path_allowed_and_not_ignored():
    assert is_indexable_path("docs/readme.md", {".md"}, ["__pycache__"])


def test_is_indexable_path_disallowed_extension():
    assert not is_indexable_path("image.png", {".md", ".txt"}, [])


def test_is_indexable_path_ignored_directory():
    assert not is_indexable_path("project/.git/HEAD", {".md", ".txt", ""}, [".git"])


def test_is_indexable_path_no_ignore_patterns():
    assert is_indexable_path("notes.txt", {".txt"})
