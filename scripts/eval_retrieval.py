"""Retrieval quality check: run retrieve_best() over labelled questions, report hit@k and MRR.

Questions live in scripts/eval_questions.yaml (gitignored — it may name personal files):

    - q: <question>
      files: [<substring of the expected filepath>]
      text: <optional string the matching chunk must also contain>

Keep real examples out of this docstring: rag-system is itself indexed, so example
questions written here would be retrieved as answers to the same eval questions.

A question scores at the rank of the first returned chunk whose filepath contains one of
`files` (and whose text contains `text`, when given). "context" is the mean size of the top
FINAL_K chunks, i.e. the retrieved part of the LLM prompt (drives prefill time).

Run inside the API image so Qdrant and Ollama are reachable (no rebuild needed):

    docker compose run --rm --no-deps -v "$PWD/scripts:/app/scripts:ro" api \\
        python -m scripts.eval_retrieval
"""

import statistics
import time
from pathlib import Path
from typing import Any

import yaml

import api.retrieval
from api.retrieval import Chunk, retrieve_best
from settings import FINAL_K

_QUESTIONS_PATH = Path(__file__).with_name("eval_questions.yaml")
_TOP_K = 10


def _rank(chunks: list[Chunk], item: dict[str, Any]) -> int | None:
    for rank, chunk in enumerate(chunks, start=1):
        path = chunk.payload.get("filepath", "")
        text = item.get("text")
        if any(f in path for f in item["files"]) and (
            not text or text in chunk.payload.get("text", "")
        ):
            return rank
    return None


def main() -> None:
    questions = yaml.safe_load(_QUESTIONS_PATH.read_text())
    api.retrieval.startup()
    ranks: list[int | None] = []
    latencies: list[float] = []
    context_chars: list[int] = []
    try:
        for i, item in enumerate(questions):
            start = time.perf_counter()
            chunks = retrieve_best(item["q"], final_k=_TOP_K)
            elapsed = time.perf_counter() - start
            if i:  # the first query also pays for loading the reranker model
                latencies.append(elapsed)
            rank = _rank(chunks, item)
            ranks.append(rank)
            context_chars.append(sum(len(c.payload.get("text", "")) for c in chunks[:FINAL_K]))
            print(f"{rank or '-':>3}  {elapsed * 1000:6.0f} ms  {item['q']}")
    finally:
        api.retrieval.shutdown()

    def hit_rate(k: int) -> float:
        return sum(1 for r in ranks if r is not None and r <= k) / len(ranks)

    mrr = sum(1 / r for r in ranks if r is not None) / len(ranks)
    print(
        f"\nquestions={len(ranks)} hit@1={hit_rate(1):.2f} hit@{FINAL_K}={hit_rate(FINAL_K):.2f} "
        f"MRR@{_TOP_K}={mrr:.3f} latency mean={statistics.mean(latencies) * 1000:.0f} ms "
        f"max={max(latencies) * 1000:.0f} ms "
        f"context mean={statistics.mean(context_chars):.0f} chars"
    )


if __name__ == "__main__":
    main()
