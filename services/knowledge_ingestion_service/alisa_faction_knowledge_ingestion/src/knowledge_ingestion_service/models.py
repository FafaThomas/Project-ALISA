from dataclasses import dataclass, field, asdict
from typing import Any

@dataclass
class Artifact:
    artifact_id: str
    faction_id: str
    collection_name: str
    relative_path: str
    content_hash: str
    extractor: str
    media_type: str
    text: str
    summary: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

@dataclass
class Chunk:
    chunk_id: str
    artifact_id: str
    faction_id: str
    collection_name: str
    source_id: str
    relative_path: str
    content_hash: str
    ordinal: int
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def payload(self):
        return {
            "artifact_id": self.artifact_id,
            "faction_id": self.faction_id,
            "collection_name": self.collection_name,
            "source_id": self.source_id,
            "relative_path": self.relative_path,
            "content_hash": self.content_hash,
            "ordinal": self.ordinal,
            "text": self.text,
            **self.metadata,
        }

@dataclass
class IngestionReport:
    faction_id: str
    collection_name: str
    root: str
    complete: bool = False
    discovered: int = 0
    extracted: int = 0
    skipped: int = 0
    failed: int = 0
    indexed_chunks: int = 0
    stale_points_removed: int = 0
    files: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)
