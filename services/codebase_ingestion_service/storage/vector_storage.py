from embedders.ollama_embedder import OllamaEmbedder
from database.qdrant_client import QdrantDatabase
from dotenv import load_dotenv

load_dotenv()

import hashlib
import os
import uuid


class VectorStorage:

    def __init__(self):

        self.embedder = OllamaEmbedder()

        self.database = QdrantDatabase()

    def collection_name(self, project_name: str) -> str:

        suffix = os.getenv(
            "CODEBASE_COLLECTION_SUFFIX",
            "_codebase"
        )

        return (
            project_name
            .lower()
            .replace("-", "_")
            .replace(" ", "_")
            + suffix
        )

    def store(
        self,
        project_name: str,
        parsed_documents,
    ):

        collection = self.collection_name(project_name)

        self.database.create_collection(collection)

        source_files = 0
        manifest_files = 0

        source_chunks = 0
        manifest_chunks = 0

        empty_files = 0
        skipped_chunks = 0

        vectors_stored = 0

        points = []

        documents_count = len(parsed_documents)
        chunks_found = 0
        chunks_skipped = 0

        for document in parsed_documents:

            if document.language == "manifest":
                manifest_files += 1
            else:
                source_files += 1

            if not document.chunks:
                empty_files += 1
                continue 

            for chunk in document.chunks:

                if not chunk.content.strip():
                    skipped_chunks += 1
                    continue

                if document.language == "manifest":
                    manifest_chunks += 1
                else:
                    source_chunks += 1

                if not document.chunks:
                    empty_files += 1
                    continue

                chunks_found += 1

                if not chunk.content.strip():
                    chunks_skipped += 1
                    print(
                        f"Skipped empty chunk: "
                        f"{document.relative_path} "
                        f"({chunk.name})"
                    )
                    continue

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

                vectors_stored += 1

        self.database.upsert(
            collection,
            points,
        )



        print()
        print("========================================")
        print("Embedding Summary")
        print("========================================")
        print()

        print(f"Collection           : {collection}")
        print()

        print(f"Source Files         : {source_files}")
        print(f"Manifest Files       : {manifest_files}")
        print()

        print(f"Source Chunks        : {source_chunks}")
        print(f"Manifest Chunks      : {manifest_chunks}")
        print()

        print(f"Empty Files          : {empty_files}")
        print(f"Skipped Chunks       : {skipped_chunks}")
        print()

        print(f"Total Vectors Stored : {vectors_stored}")
        print()

    def payload(
        self,
        project_name,
        document,
        chunk,
    ):

        return {

            "project": project_name,

            "relative_path": str(document.relative_path),

            "language": document.language,

            "chunk_name": chunk.name,

            "chunk_type": chunk.type,

            "start_line": chunk.start_line,

            "end_line": chunk.end_line,

            "content": chunk.content,

            "imports": document.imports,
        }
    
    def chunk_id(
        self,
        project,
        path,
        chunk,
    ):

        text = (
            f"{project}"
            f"{path}"
            f"{chunk.name}"
            f"{chunk.start_line}"
            f"{chunk.end_line}"
        )

        return str(
            uuid.uuid5(
                uuid.NAMESPACE_DNS,
                text,
            )
        )