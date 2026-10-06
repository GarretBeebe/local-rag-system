"""
Session-wide mock that keeps unit tests from loading the reranker model.

sentence_transformers is replaced before any module imports it, so
CrossEncoder(...) in api/retrieval.py returns a MagicMock instead of loading
model weights.
"""

import sys
from unittest.mock import MagicMock

sys.modules.setdefault("sentence_transformers", MagicMock())
