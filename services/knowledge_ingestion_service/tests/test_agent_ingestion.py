import pytest

from src.knowledge_ingestion_service.agent_context import AgentIdentity
from src.knowledge_ingestion_service.pipeline import IngestionPipeline


class StaticAgentProvider:
    def __init__(self, agent_id):
        self.agent_id = agent_id

    def resolve_current_agent_id(self):
        return self.agent_id


class StaticAgentRegistry:
    def __init__(self, identities):
        self.identities = identities

    def get_agent(self, agent_id):
        return self.identities.get(agent_id)


class CaptureIndexer:
    def __init__(self):
        self.chunks = []

    def upsert(self, chunks):
        self.chunks = list(chunks)
        return len(chunks)

    def reconcile_source(self, source_id, expected_chunk_ids):
        return 0


def test_agent_ingestion_records_provenance_on_content_and_stubs(tmp_path):
    (tmp_path / "main.py").write_text("print('hello')", encoding="utf-8")
    (tmp_path / "model.stl").write_bytes(b"metadata-only geometry")

    indexer = CaptureIndexer()
    pipeline = IngestionPipeline(
        indexer=indexer,
        agent_provider=StaticAgentProvider("codebase_interpreter_agent"),
        agent_registry=StaticAgentRegistry({
            "codebase_interpreter_agent": AgentIdentity(
                agent_id="codebase_interpreter_agent",
                faction_id="forge",
            )
        }),
    )

    report = pipeline.run_for_current_agent(tmp_path, source_id="agent-source")

    assert report.complete
    assert indexer.chunks
    assert all(c.agent_id == "codebase_interpreter_agent" for c in indexer.chunks)
    assert all(c.payload()["agent_id"] == "codebase_interpreter_agent" for c in indexer.chunks)
    assert {c.faction_id for c in indexer.chunks} == {"forge"}
    assert {c.source_id for c in indexer.chunks} == {"agent-source"}
    assert any(c.payload().get("record_type") == "artifact_stub" for c in indexer.chunks)


def test_agent_ingestion_rejects_missing_provider(tmp_path):
    pipeline = IngestionPipeline(agent_registry=StaticAgentRegistry({}))
    with pytest.raises(RuntimeError, match="trusted agent_provider"):
        pipeline.run_for_current_agent(tmp_path)


def test_agent_ingestion_rejects_missing_registry(tmp_path):
    pipeline = IngestionPipeline(agent_provider=StaticAgentProvider("some-agent"))
    with pytest.raises(RuntimeError, match="authoritative agent_registry"):
        pipeline.run_for_current_agent(tmp_path)


def test_agent_ingestion_rejects_missing_authenticated_identity(tmp_path):
    pipeline = IngestionPipeline(
        agent_provider=StaticAgentProvider(None),
        agent_registry=StaticAgentRegistry({}),
    )
    with pytest.raises(PermissionError, match="authenticated ALISA agent ID"):
        pipeline.run_for_current_agent(tmp_path)


def test_agent_ingestion_rejects_unknown_agent(tmp_path):
    pipeline = IngestionPipeline(
        agent_provider=StaticAgentProvider("unknown-agent"),
        agent_registry=StaticAgentRegistry({}),
    )
    with pytest.raises(PermissionError, match="Unknown ALISA agent"):
        pipeline.run_for_current_agent(tmp_path)


def test_agent_ingestion_rejects_registry_identity_mismatch(tmp_path):
    pipeline = IngestionPipeline(
        agent_provider=StaticAgentProvider("authenticated-agent"),
        agent_registry=StaticAgentRegistry({
            "authenticated-agent": AgentIdentity(
                agent_id="different-agent",
                faction_id="forge",
            )
        }),
    )
    with pytest.raises(PermissionError, match="does not match"):
        pipeline.run_for_current_agent(tmp_path)


def test_agent_registry_controls_faction_assignment(tmp_path):
    (tmp_path / "main.py").write_text("print('faction test')", encoding="utf-8")
    indexer = CaptureIndexer()
    pipeline = IngestionPipeline(
        indexer=indexer,
        agent_provider=StaticAgentProvider("registered-agent"),
        agent_registry=StaticAgentRegistry({
            "registered-agent": AgentIdentity(
                agent_id="registered-agent",
                faction_id="pentagram",
            )
        }),
    )

    report = pipeline.run_for_current_agent(tmp_path, source_id="registered-source")

    assert report.complete
    assert report.faction_id == "pentagram"
    assert {chunk.faction_id for chunk in indexer.chunks} == {"pentagram"}
    assert {chunk.collection_name for chunk in indexer.chunks} == {"alisa_faction_pentagram"}


def test_manual_ingestion_keeps_agent_provenance_empty(tmp_path):
    (tmp_path / "main.py").write_text("print('manual')", encoding="utf-8")
    indexer = CaptureIndexer()
    pipeline = IngestionPipeline(indexer=indexer)
    report = pipeline.run(tmp_path, "forge", "manual-source")
    assert report.complete
    assert indexer.chunks
    assert all(c.agent_id is None for c in indexer.chunks)
    assert all(c.payload()["agent_id"] is None for c in indexer.chunks)


def test_extractor_metadata_cannot_override_agent_provenance():
    from src.knowledge_ingestion_service.models import Chunk

    chunk = Chunk(
        chunk_id="chunk-1",
        artifact_id="artifact-1",
        faction_id="forge",
        collection_name="alisa_faction_forge",
        source_id="source-1",
        relative_path="main.py",
        content_hash="hash",
        ordinal=0,
        text="example",
        metadata={"agent_id": "spoofed-agent"},
        agent_id="trusted-agent",
    )
    assert chunk.payload()["agent_id"] == "trusted-agent"

