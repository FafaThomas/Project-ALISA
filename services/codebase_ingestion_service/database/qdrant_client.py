from qdrant_client import QdrantClient


from qdrant_client.http.models import (
    VectorParams,
    Distance,
)


class QdrantDatabase:

    def __init__(self):

        self.client = QdrantClient(
            host="localhost",
            port=6333,
        )

    def collection_exists(
        self,
        collection: str,
    ) -> bool:

        collections = self.client.get_collections()

        names = {
            c.name
            for c in collections.collections
        }

        return collection in names

    def create_collection(
        self,
        collection: str,
        dimensions: int = 768,
    ):

        if self.collection_exists(collection):

            return

        self.client.create_collection(
            collection_name=collection,
            vectors_config=VectorParams(
                size=dimensions,
                distance=Distance.COSINE,
            ),
        )

    def upsert(
        self,
        collection: str,
        points,
    ):

        self.client.upsert(
            collection_name=collection,
            points=points,
        )