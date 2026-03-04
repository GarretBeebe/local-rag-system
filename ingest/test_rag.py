import requests
import uuid

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct

OLLAMA_URL = "http://localhost:11434/api/embeddings"

MODEL = "nomic-embed-text"
COLLECTION = "documents"

client = QdrantClient(host="localhost", port=6333)


def embed(text):

    response = requests.post(
        OLLAMA_URL,
        json={
            "model": MODEL,
            "prompt": text
        },
    )

    return response.json()["embedding"]


def create_collection(vector_size):

    if not client.collection_exists(COLLECTION):

        client.create_collection(
            collection_name=COLLECTION,
            vectors_config=VectorParams(
                size=vector_size,
                distance=Distance.COSINE
            ),
        )


def store_document(text):

    vector = embed(text)

    client.upsert(
        collection_name=COLLECTION,
        points=[
            PointStruct(
                id=str(uuid.uuid4()),
                vector=vector,
                payload={"text": text}
            )
        ],
    )


def search(query):

    vector = embed(query)

    results = client.query_points(
        collection_name=COLLECTION,
        query=vector,
        limit=3
    )

    for r in results.points:
        print(r.payload["text"], "score:", r.score)


if __name__ == "__main__":

    text = "Retrieval augmented generation combines vector search with language models."

    vector = embed(text)

    create_collection(len(vector))

    store_document(text)

    search("What is RAG?")
