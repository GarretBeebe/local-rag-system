"""Shared path and file-filter helpers."""

from collections.abc import Iterable
from fnmatch import fnmatch
from pathlib import Path


def normalize_path(path: str | Path) -> str:
    """Return a normalized absolute path string."""
    return str(Path(path).expanduser().resolve())


def has_allowed_extension(path: str | Path, allowed_extensions: Iterable[str]) -> bool:
    """Return True when the path suffix is in the allowed extension set."""
    return Path(path).suffix.lower() in {ext.lower() for ext in allowed_extensions}


def matches_ignore_pattern(path: str | Path, ignore_patterns: Iterable[str]) -> bool:
    """Return True when any ignore token or glob matches a path component."""
    path_obj = Path(path)
    parts = path_obj.parts
    path_text = str(path_obj)
    normalized_path_text = path_text.replace("\\", "/")
    for pattern in ignore_patterns:
        normalized_pattern = pattern.replace("\\", "/")
        if pattern in parts:
            return True
        if any(fnmatch(part, pattern) for part in parts):
            return True
        if fnmatch(path_text, pattern):
            return True
        if (
            "/" in normalized_pattern
            and (
                fnmatch(normalized_path_text, normalized_pattern)
                or fnmatch(normalized_path_text, f"*/{normalized_pattern}")
                or fnmatch(normalized_path_text, f"*/{normalized_pattern}/*")
            )
        ):
            return True
    return False


def is_indexable_path(
    path: str | Path,
    allowed_extensions: Iterable[str],
    ignore_patterns: Iterable[str] = (),
) -> bool:
    """Return True when a path passes ignore and extension filters."""
    return not matches_ignore_pattern(path, ignore_patterns) and has_allowed_extension(
        path, allowed_extensions
    )
