try:
    from .index_documents import COLLECTION, client
except ImportError:
    from index_documents import COLLECTION, client

if client.collection_exists(COLLECTION):
    client.delete_collection(COLLECTION)

print("Collection removed.")
