import requests
from typing import List, Dict, Any

from retrieval import retrieve_best
from settings import OLLAMA_BASE_URL, GEN_MODEL


def build_prompt(question: str, chunks: List[Dict[str, Any]]) -> str:
    context_blocks = []
    for i, c in enumerate(chunks, start=1):
        p = c["payload"]
        cite = f"[S{i}] {p.get('filepath', p.get('filename', 'unknown'))} (chunk {p.get('chunk_index', '?')}/{p.get('chunk_total', '?')})"
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


def ask(question: str) -> None:
    chunks = retrieve_best(question, recall_k=30, mmr_k=10, final_k=6)
    if not chunks:
        print("No relevant context found in the vector store yet.")
        return

    prompt = build_prompt(question, chunks)
    answer = generate(prompt)

    print("\nAnswer:\n")
    print(answer.strip())

    print("\nSources:\n")
    for i, c in enumerate(chunks, start=1):
        p = c["payload"]
        path = p.get("filepath", p.get("filename", "unknown"))
        print(f"[S{i}] {path}  (rerank={c.get('rerank_score', 0):.4f})")


if __name__ == "__main__":
    q = input("Ask a question: ").strip()
    ask(q)
