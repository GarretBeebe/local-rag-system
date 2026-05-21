"""Project-wide type aliases."""

from enum import StrEnum
from typing import Literal

RagMode = Literal["strict", "augmented"]


class IndexDecision(StrEnum):
    MISSING = "missing"
    UNCHANGED = "unchanged"
    INDEXED = "indexed"
    SKIPPED = "skipped"
    FAILED = "failed"
