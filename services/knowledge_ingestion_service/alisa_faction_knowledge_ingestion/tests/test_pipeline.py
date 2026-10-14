import json
import pytest
from knowledge_ingestion_service.pipeline import IngestionPipeline
from knowledge_ingestion_service.factions import FactionRegistry, Faction

def test_routes_to_faction_specific_collection(tmp_path):
    (tmp_path / "main.py").write_text("print('hello')", encoding="utf-8")
    class CaptureIndexer:
        def __init__(self): self.chunks = []
        def upsert(self, chunks): self.chunks.extend(chunks); return len(chunks)
        def reconcile(self, source_id, current_artifacts): return 0
    indexer = CaptureIndexer()
    report = IngestionPipeline(indexer=indexer).run(tmp_path, "forge")
    assert report.collection_name == "alisa_faction_forge"
    assert indexer.chunks[0].faction_id == "forge"
    assert indexer.chunks[0].collection_name == "alisa_faction_forge"

def test_unknown_faction_rejected(tmp_path):
    with pytest.raises(ValueError):
        IngestionPipeline().run(tmp_path, "not-a-real-faction")

def test_secrets_excluded(tmp_path):
    (tmp_path / ".env").write_text("API_KEY=secret", encoding="utf-8")
    (tmp_path / "ok.py").write_text("print('ok')", encoding="utf-8")
    report = IngestionPipeline().run(tmp_path, "forge")
    assert report.discovered == 1
    assert report.extracted == 1

def test_csv_profile(tmp_path):
    (tmp_path / "data.csv").write_text("time,kw\n10:00,2.1\n11:00,2.4\n", encoding="utf-8")
    out = tmp_path / "report.json"
    report = IngestionPipeline().run(tmp_path, "pentagram", out)
    assert report.extracted == 1
    assert json.loads(out.read_text())["collection_name"] == "alisa_faction_pentagram"
