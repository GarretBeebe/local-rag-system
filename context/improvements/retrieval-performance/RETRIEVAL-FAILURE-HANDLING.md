# Retrieval Failure Handling Design Notes

Extracted from `context/improvements/review-backlog/GRADE-IT-FINDINGS.md` so the findings backlog can stay concise while preserving the implementation design for later work.

## Problem

`qdrant_recall()` catches every Qdrant exception, logs it, and returns `[]`. Downstream, `_prepare_query()` cannot distinguish between two fundamentally different situations that both produce an empty candidate list:

1. Qdrant searched successfully and found nothing: legitimate empty result; augmented fallback is correct behavior.
2. Qdrant threw an exception: infrastructure failure; falling back silently masks the outage.

In augmented mode, case 2 produces a normal-looking model-only answer with no signal to the user that RAG was unavailable. In strict mode, the behavior is the same as case 1 and returns `_NO_CONTEXT_REPLY`, even though the cause was backend failure rather than absent documents.

## Proposed Behavior

Introduce a `RetrievalUnavailable` exception and raise it from `qdrant_recall()` on backend failure instead of catching and returning `[]`. Handle it explicitly in `_prepare_query()` per mode.

BM25 failure in `hybrid_recall()` can stay logged-and-continued; keyword search failing is degraded but recoverable. Vector search failure is the more fundamental failure and should propagate.

## Sketch

```python
class RetrievalUnavailable(Exception):
    pass

# In qdrant_recall():
except Exception as e:
    logger.error("Qdrant recall failed: %s", e)
    raise RetrievalUnavailable(str(e)) from e

# In _prepare_query():
try:
    chunks = retrieve_best(question)
except RetrievalUnavailable:
    if rag_mode == "strict":
        return _PreparedQuery(
            prompt=None,
            direct_reply="Retrieval service is unavailable. Cannot answer in strict mode.",
        )
    return _PreparedQuery(
        prompt=question,
        sources="\n\n---\n\n*Retrieval unavailable - answer from model knowledge only.*",
    )
```

## `_PreparedQuery` Behavioral Contract

When this fix is implemented, `_PreparedQuery` gains a third meaningful state beyond its current two:

| State | `prompt` | `direct_reply` | `sources` | Meaning |
|---|---|---|---|---|
| Normal with context | built prompt | `None` | source citations | RAG answered |
| No context (augmented) | raw question | `None` | `""` | No docs found; model fallback |
| No context (strict) | `None` | static string | `""` | No docs; refuse |
| Retrieval failed (augmented) | raw question | `None` | degraded notice | Qdrant down; model fallback plus warning |
| Retrieval failed (strict) | `None` | error string | `""` | Qdrant down; refuse |

The `sources` field currently doubles as a degradation signal channel. Make that explicit in the implementation, or replace it with a dedicated `degraded: bool` / `degradation_notice: str | None` field if overloading `sources` is too muddy.
