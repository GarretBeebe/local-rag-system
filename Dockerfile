FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml .
RUN pip install --no-cache-dir \
    qdrant-client \
    sentence-transformers \
    rank-bm25 \
    langchain-text-splitters \
    watchdog \
    pyyaml \
    tqdm \
    requests \
    fastapi \
    uvicorn

COPY . .
RUN pip install --no-cache-dir -e . --no-deps

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
