import uuid
import requests
from pathlib import Path
from tqdm import tqdm

from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct, Distance, VectorParams

from langchain_text_splitters import RecursiveCharacterTextSplitter



OLLAMA_URL = "http://localhost:11434/api/embeddings"
MODEL = "nomic-embed-text"
COLLECTION = "documents"

DOCS_PATH = Path("../documents")

client = QdrantClient(host="localhost", port=6333)


splitter = RecursiveCharacterTextSplitter(
    chunk_size=500,
    chunk_overlap=100
)

COLLECTION = "documents"

VECTOR_SIZE = 768

def ensure_collection(vector_size):

    if not client.collection_exists(COLLECTION):

        print("Creating collection:", COLLECTION)

        client.create_collection(
            collection_name=COLLECTION,
            vectors_config=VectorParams(
                size=vector_size,
                distance=Distance.COSINE
            )
        )


def embed(text):

    r = requests.post(
        OLLAMA_URL,
        json={"model": MODEL, "prompt": text}
    )

    return r.json()["embedding"]


def load_files():

    files = []

    for path in DOCS_PATH.rglob("*"):

        if path.suffix in [".md", ".txt", ".py", ".json", ".yaml", ".yml"]:
            files.append(path)

    return files


def index_file(path):

    ensure_collection()

    text = path.read_text()

    chunks = splitter.split_text(text)

    document_id = str(uuid.uuid5(uuid.NAMESPACE_URL, str(path)))

    points = []

    for i, chunk in enumerate(chunks):

        vector = embed(chunk)

        payload = {
            "text": chunk,
            "document_id": document_id,
            "filename": path.name,
            "filepath": str(path),
            "chunk_index": i,
            "chunk_total": len(chunks)
        }

        points.append(
            PointStruct(
                id=str(uuid.uuid4()),
                vector=vector,
                payload=payload
            )
        )

    client.upsert(
        collection_name=COLLECTION,
        points=points
    )

def delete_document(filepath):
    """
    Remove all chunks belonging to a file from Qdrant.
    """

    print(f"Deleting vectors for: {filepath}")

    from qdrant_client.models import Filter, FieldCondition, MatchValue

    client.delete(
        collection_name=COLLECTION,
        points_selector=Filter(
            must=[
                FieldCondition(
                    key="filepath",
                    match=MatchValue(value=filepath)
                )
            ]
        )
    )

def main():

    files = load_files()

    print(f"Indexing {len(files)} files")

    if len(files) == 0:
        return

    # get vector size from first chunk
    first_text = files[0].read_text()
    first_chunk = splitter.split_text(first_text)[0]
    vector = embed(first_chunk)

    ensure_collection(len(vector))
    
    for f in files:
        print("Found:", f)    

    for f in tqdm(files):

        index_file(f)


if __name__ == "__main__":
    main()
