from embedders.ollama_embedder import OllamaEmbedder
from database.qdrant_client import QdrantDatabase
from dotenv import load_dotenv

load_dotenv()

import os
import uuid


class VectorStorage:

    MAX_CHUNK_CHARS = 6000

    def __init__(self):

        self.embedder = OllamaEmbedder()

        self.database = QdrantDatabase()

    def collection_name(
        self,
        project_name: str
    ) -> str:

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

        collection = self.collection_name(
            project_name
        )

        self.database.create_collection(
            collection
        )

        source_files = 0
        manifest_files = 0

        source_chunks = 0
        manifest_chunks = 0

        empty_files = 0
        skipped_chunks = 0

        vectors_stored = 0

        points = []

        documents_count = len(
            parsed_documents
        )

        chunks_found = 0
        chunks_skipped = 0

        split_chunks = 0
        split_parts = 0

        failed_chunks = 0

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

                chunks_found += 1

                content = chunk.content

                if len(content) <= self.MAX_CHUNK_CHARS:

                    try:

                        vector = self.embedder.embed(
                            content
                        )

                    except Exception as e:

                        failed_chunks += 1

                        print()

                        print(
                            "Failed to embed chunk:"
                        )

                        print(
                            f"  File    : "
                            f"{document.relative_path}"
                        )

                        print(
                            f"  Chunk   : "
                            f"{chunk.name}"
                        )

                        print(
                            f"  Type    : "
                            f"{chunk.type}"
                        )

                        print(
                            f"  Lines   : "
                            f"{chunk.start_line}-"
                            f"{chunk.end_line}"
                        )

                        print(
                            f"  Size    : "
                            f"{len(content)} chars"
                        )

                        print(
                            f"  Error   : {e}"
                        )

                        continue

                    payload = self.payload(
                        project_name,
                        document,
                        chunk,
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

                    continue

                print()

                print(
                    "Splitting oversized chunk:"
                )

                print(
                    f"  File    : "
                    f"{document.relative_path}"
                )

                print(
                    f"  Chunk   : "
                    f"{chunk.name}"
                )

                print(
                    f"  Type    : "
                    f"{chunk.type}"
                )

                print(
                    f"  Size    : "
                    f"{len(content)} chars"
                )

                split_chunks += 1

                parts = self.split_content(
                    content
                )

                for part_index, part in enumerate(
                    parts,
                    start=1,
                ):

                    try:

                        vector = self.embedder.embed(
                            part
                        )

                    except Exception as e:

                        failed_chunks += 1

                        print()

                        print(
                            "Failed to embed split part:"
                        )

                        print(
                            f"  File    : "
                            f"{document.relative_path}"
                        )

                        print(
                            f"  Chunk   : "
                            f"{chunk.name}"
                        )

                        print(
                            f"  Part    : "
                            f"{part_index}/"
                            f"{len(parts)}"
                        )

                        print(
                            f"  Size    : "
                            f"{len(part)} chars"
                        )

                        print(
                            f"  Error   : {e}"
                        )

                        continue

                    payload = self.payload(
                        project_name,
                        document,
                        chunk,
                    )

                    payload["chunk_part"] = (
                        part_index
                    )

                    payload["chunk_parts"] = (
                        len(parts)
                    )

                    payload["content"] = part

                    points.append({

                        "id": self.chunk_part_id(
                            project_name,
                            document.relative_path,
                            chunk,
                            part_index,
                        ),

                        "vector": vector,

                        "payload": payload,

                    })

                    vectors_stored += 1

                    split_parts += 1

        if points:

            BATCH_SIZE = int(
                os.getenv(
                    "QDRANT_BATCH_SIZE",
                    "50",
                )
            )

            total_batches = (
                (len(points) + BATCH_SIZE - 1)
                // BATCH_SIZE
            )

            successful_batches = 0
            failed_batches = 0

            print()
            print("========================================")
            print("Qdrant Upload")
            print("========================================")
            print()

            print(f"Total Points  : {len(points)}")
            print(f"Batch Size    : {BATCH_SIZE}")
            print(f"Total Batches : {total_batches}")
            print()

            for i in range(
                0,
                len(points),
                BATCH_SIZE,
            ):

                batch = points[
                    i:i + BATCH_SIZE
                ]

                batch_number = (
                    (i // BATCH_SIZE) + 1
                )

                print(
                    f"Uploading batch "
                    f"{batch_number}/{total_batches} "
                    f"({len(batch)} points)..."
                )

                try:

                    self.database.upsert(
                        collection,
                        batch,
                    )

                    successful_batches += 1

                except Exception as e:

                    failed_batches += 1

                    print(
                        f"FAILED batch "
                        f"{batch_number}/{total_batches}: "
                        f"{e}"
                    )

            print()
            print("Qdrant Upload Complete")
            print(
                f"Successful Batches : "
                f"{successful_batches}"
            )
            print(
                f"Failed Batches     : "
                f"{failed_batches}"
            )

        print()

        print(
            "========================================"
        )

        print(
            "Embedding Summary"
        )

        print(
            "========================================"
        )

        print()

        print(
            f"Collection           : {collection}"
        )

        print()

        print(
            f"Source Files         : {source_files}"
        )

        print(
            f"Manifest Files       : {manifest_files}"
        )

        print()

        print(
            f"Source Chunks        : {source_chunks}"
        )

        print(
            f"Manifest Chunks      : {manifest_chunks}"
        )

        print()

        print(
            f"Empty Files          : {empty_files}"
        )

        print(
            f"Skipped Chunks       : {skipped_chunks}"
        )

        print()

        print(
            f"Oversized Chunks     : {split_chunks}"
        )

        print(
            f"Split Parts          : {split_parts}"
        )

        print(
            f"Failed Chunks        : {failed_chunks}"
        )

        print()

        print(
            f"Total Vectors Stored : {vectors_stored}"
        )

        print()

    def split_content(
        self,
        content: str,
    ):

        parts = []

        start = 0

        length = len(content)

        while start < length:

            end = min(
                start + self.MAX_CHUNK_CHARS,
                length,
            )

            if end < length:

                newline = content.rfind(
                    "\n",
                    start,
                    end,
                )

                if newline > start:

                    end = newline

            part = content[
                start:end
            ].strip()

            if part:

                parts.append(
                    part
                )

            start = end

        return parts

    def payload(
        self,
        project_name,
        document,
        chunk,
    ):

        return {

            "project": project_name,

            "relative_path": str(
                document.relative_path
            ),

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

    def chunk_part_id(
        self,
        project,
        path,
        chunk,
        part_index,
    ):

        text = (
            f"{project}"
            f"{path}"
            f"{chunk.name}"
            f"{chunk.start_line}"
            f"{chunk.end_line}"
            f"part:{part_index}"
        )

        return str(
            uuid.uuid5(
                uuid.NAMESPACE_DNS,
                text,
            )
        )
