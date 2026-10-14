# ALISA Faction Knowledge Ingestion v0.3

Shared ingestion + faction-scoped collections + source-scoped reconciliation + semantic retrieval.

## Key rules
- Each faction owns one Qdrant collection.
- Every indexed point carries `faction_id`, `source_id`, `artifact_id`, and `content_hash`.
- Reconciliation only deletes stale points belonging to the source being rescanned.
- The default source ID is a stable hash of the resolved root path. Pass `--source-id` to assign an explicit stable source namespace.
- Cross-faction retrieval is explicit and searches only the collections requested.
- The source of truth remains the original artifact, not the vector database.
- Unsupported formats are reported. CSVs are profiled and sampled; rows are not individually embedded.
- Credentials and common secret files are excluded.

## Install
```powershell
python -m pip install -e ".[test]"
python -m pytest -q
python -m pip install -e ".[qdrant]"
```

## Ingest without indexing (safe smoke test)
```powershell
knowledge-ingest "D:\path\to\small-test-workspace" --faction forge --report .\report.json
```

## Ingest and index
```powershell
knowledge-ingest "D:\path\to\small-test-workspace" --faction forge --source-id forge-docs-dev --index
```

## Retrieve
```powershell
knowledge-search "how does this repository configure its database?" --faction forge --top-k 5
knowledge-search "what is a PID controller?" --factions forge,pentagram --top-k 8
```

Environment:
- `ALISA_QDRANT_URL` default `http://127.0.0.1:6333`
- `ALISA_OLLAMA_URL` default `http://localhost:11434`
- `ALISA_EMBED_MODEL` default `nomic-embed-text:latest`
- `ALISA_QDRANT_PREFIX` default `alisa_faction_`

The default registry contains `forge`, `pentagram`, `corpus`, and `knife`. Edit the registry to add or rename factions before ingestion. Never reuse a faction ID for a different semantic domain without considering its existing collection.
