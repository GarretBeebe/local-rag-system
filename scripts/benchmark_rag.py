"""Small benchmark helper for end-to-end API query latency.

Examples:
    uv run python scripts/benchmark_rag.py query --questions "What is this system?"
    uv run python scripts/benchmark_rag.py query --concurrency 2 --repeat 5
"""

import argparse
import concurrent.futures
import statistics
import time
from typing import Any

import requests

DEFAULT_QUESTIONS = [
    "What does this system do?",
    "How does document ingestion work?",
    "How does retrieval combine vector and keyword search?",
]


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, round((len(ordered) - 1) * pct))
    return ordered[idx]


def _post_question(base_url: str, model: str, question: str, token: str | None) -> float:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    payload: dict[str, Any] = {
        "model": model,
        "stream": False,
        "messages": [{"role": "user", "content": question}],
    }
    start = time.perf_counter()
    response = requests.post(
        f"{base_url.rstrip('/')}/v1/chat/completions",
        json=payload,
        headers=headers,
        timeout=300,
    )
    elapsed = time.perf_counter() - start
    response.raise_for_status()
    return elapsed


def benchmark_query(args: argparse.Namespace) -> None:
    questions = args.questions or DEFAULT_QUESTIONS
    work = [q for _ in range(args.repeat) for q in questions]
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = [
            executor.submit(_post_question, args.base_url, args.model, q, args.token) for q in work
        ]
        timings = [f.result() for f in concurrent.futures.as_completed(futures)]

    print(f"requests={len(timings)} concurrency={args.concurrency}")
    print(f"mean={statistics.mean(timings):.2f}s")
    print(f"p50={statistics.median(timings):.2f}s")
    print(f"p95={_percentile(timings, 0.95):.2f}s")
    print(f"max={max(timings):.2f}s")


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark local RAG operations")
    sub = parser.add_subparsers(required=True)

    query = sub.add_parser("query", help="Benchmark API query latency")
    query.add_argument("--base-url", default="http://localhost:8000")
    query.add_argument("--model", default="qwen2.5:14b")
    query.add_argument("--token")
    query.add_argument("--concurrency", type=int, default=1)
    query.add_argument("--repeat", type=int, default=1)
    query.add_argument("--questions", nargs="*")
    query.set_defaults(func=benchmark_query)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
