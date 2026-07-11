from embedders.ollama_embedder import OllamaEmbedder
from database.qdrant_client import QdrantDatabase

import hashlib
import os


class VectorStorage:

    def __init__(self):

        self.embedder = OllamaEmbedder()

        self.database = QdrantDatabase()

    def store(
        self,
        project_name: str,
        parsed_documents,
    ):

        collection = self.collection_name(project_name)

        self.database.create_collection(collection)

        points = []

        for document in parsed_documents:

            for chunk in document.chunks:

                vector = self.embedder.embed(
                    chunk.content
                )

                payload = self.payload(
                    project_name,
                    document,
                    chunk
                )

                points.append({
                    "id": self.chunk_id(
                        project_name,
                        document.relative_path,
                        chunk,
                    ),

                    "vector": vector,

                    "payload": payload,
                })

        self.database.upsert(
            collection,
            points,
        )