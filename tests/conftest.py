"""
Session-wide mocks that prevent heavy imports from loading models or
hitting Qdrant during unit tests.

After Phase 4 (import-time side effects removed), CrossEncoder and
KeywordIndex._build will be lazy, so these patches can be removed.
"""

import sys
from unittest.mock import MagicMock

# Intercept sentence_transformers before any module imports it, so
# CrossEncoder(RERANK_MODEL, device="cpu") in api/retrieval.py returns
# a MagicMock instead of loading a 400MB model.
sys.modules.setdefault("sentence_transformers", MagicMock())

# Patch KeywordIndex._build and _refresh_loop so KeywordIndex() in
# api/retrieval.py does not scroll Qdrant at construction time.
import api.keyword_index  # noqa: E402


def _noop_build(self):
    with self._lock:
        self.docs, self.meta, self.ids, self.bm25 = [], [], [], None


api.keyword_index.KeywordIndex._build = _noop_build
api.keyword_index.KeywordIndex._refresh_loop = lambda self, interval: None
