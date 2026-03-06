"""
RAG query interface: retrieves relevant chunks and generates an answer via Ollama.

ask(question) runs the full pipeline:
  1. Retrieve top-ranked chunks via retrieval.retrieve_best()
  2. Build a citation-aware prompt
  3. Send the prompt to the local LLM and print the response with source citations

Can be run directly as a script for interactive querying:
  python api/query_rag.py
"""

from typing import Any

import requests

from api.retrieval import retrieve_best
from settings import GEN_MODEL, OLLAMA_BASE_URL


def build_prompt(question: str, chunks: list[dict[str, Any]]) -> str:
    context_blocks = []
    for i, c in enumerate(chunks, start=1):
        p = c["payload"]
        source = p.get("filepath", p.get("filename", "unknown"))
        chunk_ref = f"{p.get('chunk_index', '?')}/{p.get('chunk_total', '?')}"
        cite = f"[S{i}] {source} (chunk {chunk_ref})"
        context_blocks.append(f"{cite}\n{p['text']}")

    context = "\n\n---\n\n".join(context_blocks)

    return f"""You are a careful assistant. Use ONLY the context below to answer.
If the context is insufficient, say what is missing.

Context:
{context}

Question:
{question}

Instructions:
- Be concise.
- Cite sources like [S1], [S2] in the answer.
- Do not invent details not present in the context.

Answer:
"""


def generate(prompt: str) -> str:
    r = requests.post(
        f"{OLLAMA_BASE_URL}/api/generate",
        json={"model": GEN_MODEL, "prompt": prompt, "stream": False},
        timeout=120,
    )
    r.raise_for_status()
    return r.json()["response"]


def ask(question: str) -> str:
    chunks = retrieve_best(question, recall_k=30, mmr_k=10, final_k=6)

    if not chunks:
        return "No relevant context found in the vector store yet."

    prompt = build_prompt(question, chunks)
    answer = generate(prompt).strip()

    if "Answer:" in answer:
        answer = answer.split("Answer:", 1)[1].strip()

    # build sources section
    sources = []
    for i, c in enumerate(chunks, start=1):
        p = c["payload"]
        path = p.get("filepath", p.get("filename", "unknown"))
        score = c.get("rerank_score", 0)
        sources.append(f"[S{i}] {path} (rerank={score:.4f})")

    return f"""{answer}

---

Sources:

{chr(10).join(sources)}
"""


if __name__ == "__main__":
    q = input("Ask a question: ").strip()
    chunks = retrieve_best(q, recall_k=30, mmr_k=10, final_k=6)
    if not chunks:
        print("No relevant context found in the vector store yet.")
    else:
        prompt = build_prompt(q, chunks)
        print("\nAnswer:\n")
        print(generate(prompt).strip())
        print("\nSources:\n")
        for i, c in enumerate(chunks, start=1):
            p = c["payload"]
            path = p.get("filepath", p.get("filename", "unknown"))
            print(f"[S{i}] {path}  (rerank={c.get('rerank_score', 0):.4f})")
