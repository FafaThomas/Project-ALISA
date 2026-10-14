# ALISA Faction-Aware Knowledge Ingestion

A shared ingestion service that routes artifacts to a faction-owned Qdrant collection.

## Architecture
- One shared ingestion pipeline.
- One Qdrant collection per faction.
- A registry maps validated faction IDs to collection names.
- The caller specifies the target faction; the service does not guess faction ownership.
- Cross-faction retrieval is a separate explicit operation and does not merge collections.
- Reconciliation is scoped to a source namespace inside a faction collection, so scanning one project cannot delete another project’s indexed artifacts.
- This starter includes text/JSON/CSV extraction and optional Ollama + Qdrant indexing.

## Install and test
```powershell
python -m pip install -e ".[test]"
python -m pytest -q
```

Optional Qdrant integration:
```powershell
python -m pip install -e ".[qdrant]"
```

## CLI examples
```powershell
knowledge-ingest "D:\path\to\workspace" --faction forge --report .\report.json
knowledge-ingest "D:\path\to\workspace" --faction pentagram --index
```

Set `ALISA_QDRANT_URL`, `ALISA_OLLAMA_URL`, and `ALISA_EMBED_MODEL` to configure endpoints/model.
The default collection prefix is `alisa_faction_`; use a fresh dev Qdrant instance/collections for initial tests.
