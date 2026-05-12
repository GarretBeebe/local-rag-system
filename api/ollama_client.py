"""Shared HTTP session for all Ollama API calls."""

import json
from collections.abc import Iterator
from typing import Any

import requests

from settings import OLLAMA_BASE_URL

_session = requests.Session()


def post(path: str, **kwargs: Any) -> requests.Response:
    return _session.post(f"{OLLAMA_BASE_URL}{path}", **kwargs)


def get(path: str, **kwargs: Any) -> requests.Response:
    return _session.get(f"{OLLAMA_BASE_URL}{path}", **kwargs)


def _generate_payload(model: str, prompt: str, *, stream: bool) -> dict[str, Any]:
    return {"model": model, "prompt": prompt, "stream": stream, "options": {"num_ctx": 4096}}


def generate(prompt: str, model: str, timeout: float = 120.0) -> str:
    """Return a complete generated response from Ollama."""
    r = post("/api/generate", json=_generate_payload(model, prompt, stream=False), timeout=timeout)
    r.raise_for_status()
    return r.json()["response"]


def stream_generate(prompt: str, model: str, timeout: float = 120.0) -> Iterator[str]:
    """Yield text chunks from Ollama's streaming generation API."""
    with post(
        "/api/generate",
        json=_generate_payload(model, prompt, stream=True),
        stream=True,
        timeout=timeout,
    ) as resp:
        resp.raise_for_status()
        for line in resp.iter_lines(decode_unicode=True):
            if not line:
                continue
            data = json.loads(line)
            if data.get("response"):
                yield data["response"]
            if data.get("done"):
                break
