from qdrant_client import QdrantClient

COLLECTION = "documents"

client = QdrantClient(host="localhost", port=6333)

if client.collection_exists(COLLECTION):
    client.delete_collection(COLLECTION)

print("collection removed")
