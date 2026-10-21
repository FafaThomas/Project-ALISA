# Codebase Report

**Project root:** `D:\Organized Projects\For Portfolio\Project-ALISA`

## Overview

| Metric | Count |
|---|---:|
| Files inventoried | 39 |
| Symbols discovered | 167 |
| Import edges | 99 |
| Local imports | 48 |
| External imports | 51 |
| Unresolved imports | 0 |
| Parse errors | 0 |

## File Inventory

- `.gitignore` — other; 2,328 bytes; status: inventoried
- `agents/forge/codebase_interpreter_agent/pyproject.toml` — toml; 521 bytes; status: inventoried
- `agents/forge/codebase_interpreter_agent/README.md` — markdown; 730 bytes; status: inventoried
- `agents/forge/codebase_interpreter_agent/src/codebase_interpreter_agent/__init__.py` — python; 143 bytes; status: analyzed
- `agents/forge/codebase_interpreter_agent/src/codebase_interpreter_agent/cli.py` — python; 5,147 bytes; status: analyzed
- `agents/forge/codebase_interpreter_agent/src/codebase_interpreter_agent/codebase_map.py` — python; 2,868 bytes; status: analyzed
- `agents/forge/codebase_interpreter_agent/src/codebase_interpreter_agent/codebase_queries.py` — python; 7,384 bytes; status: analyzed
- `agents/forge/codebase_interpreter_agent/src/codebase_interpreter_agent/dependency_resolver.py` — python; 8,610 bytes; status: analyzed
- `agents/forge/codebase_interpreter_agent/src/codebase_interpreter_agent/scanner.py` — python; 12,099 bytes; status: analyzed
- `agents/forge/codebase_interpreter_agent/tests/test_codebase_map.py` — python; 2,373 bytes; status: analyzed
- `agents/forge/codebase_interpreter_agent/tests/test_codebase_queries.py` — python; 2,147 bytes; status: analyzed
- `agents/forge/codebase_interpreter_agent/tests/test_codebase_question.py` — python; 2,133 bytes; status: analyzed
- `agents/forge/codebase_interpreter_agent/tests/test_dependency_resolver.py` — python; 3,683 bytes; status: analyzed
- `agents/forge/codebase_interpreter_agent/tests/test_scanner.py` — python; 3,400 bytes; status: analyzed
- `database/docker-compose.yml` — yaml; 1,223 bytes; status: inventoried
- `database/down.ps1` — powershell; 159 bytes; status: inventoried
- `database/up.ps1` — powershell; 160 bytes; status: inventoried
- `Development.MD` — markdown; 12,777 bytes; status: inventoried
- `README.MD` — markdown; 19,778 bytes; status: inventoried
- `services/knowledge_ingestion_service/.gitignore` — other; 56 bytes; status: inventoried
- `services/knowledge_ingestion_service/docs/MIGRATION_PLAN.md` — markdown; 1,615 bytes; status: inventoried
- `services/knowledge_ingestion_service/pyproject.toml` — toml; 555 bytes; status: inventoried
- `services/knowledge_ingestion_service/README.md` — markdown; 1,914 bytes; status: inventoried
- `services/knowledge_ingestion_service/src/knowledge_ingestion_service/__init__.py` — python; 209 bytes; status: analyzed
- `services/knowledge_ingestion_service/src/knowledge_ingestion_service/agent_context.py` — python; 1,103 bytes; status: analyzed
- `services/knowledge_ingestion_service/src/knowledge_ingestion_service/artifact_stubs.py` — python; 3,770 bytes; status: analyzed
- `services/knowledge_ingestion_service/src/knowledge_ingestion_service/cli.py` — python; 1,636 bytes; status: analyzed
- `services/knowledge_ingestion_service/src/knowledge_ingestion_service/extractors.py` — python; 2,697 bytes; status: analyzed
- `services/knowledge_ingestion_service/src/knowledge_ingestion_service/factions.py` — python; 1,168 bytes; status: analyzed
- `services/knowledge_ingestion_service/src/knowledge_ingestion_service/indexing.py` — python; 3,725 bytes; status: analyzed
- `services/knowledge_ingestion_service/src/knowledge_ingestion_service/models.py` — python; 1,464 bytes; status: analyzed
- `services/knowledge_ingestion_service/src/knowledge_ingestion_service/pipeline.py` — python; 18,053 bytes; status: analyzed
- `services/knowledge_ingestion_service/src/knowledge_ingestion_service/retrieval.py` — python; 3,251 bytes; status: analyzed
- `services/knowledge_ingestion_service/src/knowledge_ingestion_service/search_cli.py` — python; 1,139 bytes; status: analyzed
- `services/knowledge_ingestion_service/src/knowledge_ingestion_service/summarization.py` — python; 1,193 bytes; status: analyzed
- `services/knowledge_ingestion_service/TEST_RESULTS.txt` — other; 1,243 bytes; status: inventoried
- `services/knowledge_ingestion_service/tests/test_agent_ingestion.py` — python; 5,623 bytes; status: analyzed
- `services/knowledge_ingestion_service/tests/test_factions.py` — python; 340 bytes; status: analyzed
- `services/knowledge_ingestion_service/tests/test_pipeline.py` — python; 10,325 bytes; status: analyzed

## Symbol Index

- **function** `_load_codebase_map` — `agents/forge/codebase_interpreter_agent/src/codebase_interpreter_agent/cli.py`:15-28
- **function** `_run_scan` — `agents/forge/codebase_interpreter_agent/src/codebase_interpreter_agent/cli.py`:31-84
- **function** `main` — `agents/forge/codebase_interpreter_agent/src/codebase_interpreter_agent/cli.py`:87-177
- **function** `_normalize_path` — `agents/forge/codebase_interpreter_agent/src/codebase_interpreter_agent/codebase_map.py`:10-11
- **class** `CodebaseMap` — `agents/forge/codebase_interpreter_agent/src/codebase_interpreter_agent/codebase_map.py`:15-24
- **function** `CodebaseMap.to_dict` — `agents/forge/codebase_interpreter_agent/src/codebase_interpreter_agent/codebase_map.py`:23-24
- **function** `build_codebase_map` — `agents/forge/codebase_interpreter_agent/src/codebase_interpreter_agent/codebase_map.py`:27-104
- **function** `_normalize_path` — `agents/forge/codebase_interpreter_agent/src/codebase_interpreter_agent/codebase_queries.py`:5-6
- **function** `find_symbol` — `agents/forge/codebase_interpreter_agent/src/codebase_interpreter_agent/codebase_queries.py`:9-42
- **function** `find_dependents` — `agents/forge/codebase_interpreter_agent/src/codebase_interpreter_agent/codebase_queries.py`:45-79
- **function** `answer_codebase_question` — `agents/forge/codebase_interpreter_agent/src/codebase_interpreter_agent/codebase_queries.py`:82-254
- **class** `DependencyEdge` — `agents/forge/codebase_interpreter_agent/src/codebase_interpreter_agent/dependency_resolver.py`:13-22
- **class** `DependencyReport` — `agents/forge/codebase_interpreter_agent/src/codebase_interpreter_agent/dependency_resolver.py`:26-45
- **function** `DependencyReport.to_dict` — `agents/forge/codebase_interpreter_agent/src/codebase_interpreter_agent/dependency_resolver.py`:30-31
- **function** `DependencyReport.local_edges` — `agents/forge/codebase_interpreter_agent/src/codebase_interpreter_agent/dependency_resolver.py`:34-38
- **function** `DependencyReport.external_edges` — `agents/forge/codebase_interpreter_agent/src/codebase_interpreter_agent/dependency_resolver.py`:41-45
- **class** `_ModuleIndex` — `agents/forge/codebase_interpreter_agent/src/codebase_interpreter_agent/dependency_resolver.py`:49-53
- **function** `_module_and_root` — `agents/forge/codebase_interpreter_agent/src/codebase_interpreter_agent/dependency_resolver.py`:56-81
- **function** `_build_index` — `agents/forge/codebase_interpreter_agent/src/codebase_interpreter_agent/dependency_resolver.py`:85-131
- **function** `_relative_module` — `agents/forge/codebase_interpreter_agent/src/codebase_interpreter_agent/dependency_resolver.py`:134-155
- **function** `_find_local` — `agents/forge/codebase_interpreter_agent/src/codebase_interpreter_agent/dependency_resolver.py`:158-175
- **function** `resolve_imports` — `agents/forge/codebase_interpreter_agent/src/codebase_interpreter_agent/dependency_resolver.py`:178-289
- **function** `_is_generated_artifact` — `agents/forge/codebase_interpreter_agent/src/codebase_interpreter_agent/scanner.py`:47-55
- **class** `FileRecord` — `agents/forge/codebase_interpreter_agent/src/codebase_interpreter_agent/scanner.py`:60-65
- **class** `Symbol` — `agents/forge/codebase_interpreter_agent/src/codebase_interpreter_agent/scanner.py`:69-77
- **class** `ImportReference` — `agents/forge/codebase_interpreter_agent/src/codebase_interpreter_agent/scanner.py`:81-86
- **class** `ScanReport` — `agents/forge/codebase_interpreter_agent/src/codebase_interpreter_agent/scanner.py`:90-99
- **function** `ScanReport.to_dict` — `agents/forge/codebase_interpreter_agent/src/codebase_interpreter_agent/scanner.py`:98-99
- **function** `_excluded_dir` — `agents/forge/codebase_interpreter_agent/src/codebase_interpreter_agent/scanner.py`:102-103
- **function** `_excluded_relative_dir` — `agents/forge/codebase_interpreter_agent/src/codebase_interpreter_agent/scanner.py`:106-111
- **function** `_is_secret` — `agents/forge/codebase_interpreter_agent/src/codebase_interpreter_agent/scanner.py`:114-120
- **function** `_language` — `agents/forge/codebase_interpreter_agent/src/codebase_interpreter_agent/scanner.py`:123-159
- **function** `_name` — `agents/forge/codebase_interpreter_agent/src/codebase_interpreter_agent/scanner.py`:162-166
- **function** `_extract_python` — `agents/forge/codebase_interpreter_agent/src/codebase_interpreter_agent/scanner.py`:169-231
- **function** `_extract_python.visit` — `agents/forge/codebase_interpreter_agent/src/codebase_interpreter_agent/scanner.py`:182-228
- **function** `scan_project` — `agents/forge/codebase_interpreter_agent/src/codebase_interpreter_agent/scanner.py`:234-394
- **function** `scan_project.log` — `agents/forge/codebase_interpreter_agent/src/codebase_interpreter_agent/scanner.py`:257-260
- **function** `scan_project.on_walk_error` — `agents/forge/codebase_interpreter_agent/src/codebase_interpreter_agent/scanner.py`:262-266
- **function** `test_builds_codebase_map_with_symbols_and_dependencies` — `agents/forge/codebase_interpreter_agent/tests/test_codebase_map.py`:9-60
- **function** `test_codebase_map_serializes_to_json` — `agents/forge/codebase_interpreter_agent/tests/test_codebase_map.py`:63-87
- **function** `_make_map` — `agents/forge/codebase_interpreter_agent/tests/test_codebase_queries.py`:13-36
- **function** `test_finds_symbol_with_source_location` — `agents/forge/codebase_interpreter_agent/tests/test_codebase_queries.py`:39-52
- **function** `test_finds_dependents_of_local_module` — `agents/forge/codebase_interpreter_agent/tests/test_codebase_queries.py`:55-68
- **function** `test_missing_symbol_returns_no_matches` — `agents/forge/codebase_interpreter_agent/tests/test_codebase_queries.py`:71-77
- **function** `_make_map` — `agents/forge/codebase_interpreter_agent/tests/test_codebase_question.py`:12-33
- **function** `test_answers_where_symbol_is_defined` — `agents/forge/codebase_interpreter_agent/tests/test_codebase_question.py`:36-46
- **function** `test_answers_which_files_depend_on_symbol` — `agents/forge/codebase_interpreter_agent/tests/test_codebase_question.py`:49-62
- **function** `test_unknown_symbol_does_not_invent_evidence` — `agents/forge/codebase_interpreter_agent/tests/test_codebase_question.py`:65-74
- **function** `test_resolves_absolute_local_imports` — `agents/forge/codebase_interpreter_agent/tests/test_dependency_resolver.py`:8-34
- **function** `test_resolves_relative_imports` — `agents/forge/codebase_interpreter_agent/tests/test_dependency_resolver.py`:37-65
- **function** `test_classifies_standard_library_and_unresolved_relative_import` — `agents/forge/codebase_interpreter_agent/tests/test_dependency_resolver.py`:68-95
- **function** `test_resolves_src_prefixed_imports` — `agents/forge/codebase_interpreter_agent/tests/test_dependency_resolver.py`:98-125
- **function** `test_scans_python_symbols_and_imports` — `agents/forge/codebase_interpreter_agent/tests/test_scanner.py`:6-26
- **function** `test_excludes_secrets_and_generated_directories` — `agents/forge/codebase_interpreter_agent/tests/test_scanner.py`:29-39
- **function** `test_parse_error_is_reported_without_aborting_scan` — `agents/forge/codebase_interpreter_agent/tests/test_scanner.py`:42-50
- **function** `test_large_file_is_skipped` — `agents/forge/codebase_interpreter_agent/tests/test_scanner.py`:53-59
- **function** `test_invalid_root_and_size_limit_are_rejected` — `agents/forge/codebase_interpreter_agent/tests/test_scanner.py`:62-69
- **function** `test_excludes_packaging_metadata_and_analysis_artifacts` — `agents/forge/codebase_interpreter_agent/tests/test_scanner.py`:71-109
- **class** `AgentIdentity` — `services/knowledge_ingestion_service/src/knowledge_ingestion_service/agent_context.py`:13-17
- **class** `AgentIdentityProvider` — `services/knowledge_ingestion_service/src/knowledge_ingestion_service/agent_context.py`:20-25
- **function** `AgentIdentityProvider.resolve_current_agent_id` — `services/knowledge_ingestion_service/src/knowledge_ingestion_service/agent_context.py`:23-25
- **class** `AgentRegistry` — `services/knowledge_ingestion_service/src/knowledge_ingestion_service/agent_context.py`:28-33
- **function** `AgentRegistry.get_agent` — `services/knowledge_ingestion_service/src/knowledge_ingestion_service/agent_context.py`:31-33
- **function** `infer_artifact_category` — `services/knowledge_ingestion_service/src/knowledge_ingestion_service/artifact_stubs.py`:58-72
- **function** `make_artifact_stub` — `services/knowledge_ingestion_service/src/knowledge_ingestion_service/artifact_stubs.py`:75-126
- **function** `main` — `services/knowledge_ingestion_service/src/knowledge_ingestion_service/cli.py`:6-27
- **function** `_read_text` — `services/knowledge_ingestion_service/src/knowledge_ingestion_service/extractors.py`:34-42
- **function** `extract` — `services/knowledge_ingestion_service/src/knowledge_ingestion_service/extractors.py`:45-71
- **class** `Faction` — `services/knowledge_ingestion_service/src/knowledge_ingestion_service/factions.py`:4-7
- **class** `FactionRegistry` — `services/knowledge_ingestion_service/src/knowledge_ingestion_service/factions.py`:9-27
- **function** `FactionRegistry.__init__` — `services/knowledge_ingestion_service/src/knowledge_ingestion_service/factions.py`:11-18
- **function** `FactionRegistry.get` — `services/knowledge_ingestion_service/src/knowledge_ingestion_service/factions.py`:20-24
- **function** `FactionRegistry.all` — `services/knowledge_ingestion_service/src/knowledge_ingestion_service/factions.py`:26-27
- **class** `NoopIndexer` — `services/knowledge_ingestion_service/src/knowledge_ingestion_service/indexing.py`:1-5
- **function** `NoopIndexer.upsert` — `services/knowledge_ingestion_service/src/knowledge_ingestion_service/indexing.py`:2-3
- **function** `NoopIndexer.reconcile_source` — `services/knowledge_ingestion_service/src/knowledge_ingestion_service/indexing.py`:4-5
- **class** `OllamaQdrantIndexer` — `services/knowledge_ingestion_service/src/knowledge_ingestion_service/indexing.py`:7-94
- **function** `OllamaQdrantIndexer.__init__` — `services/knowledge_ingestion_service/src/knowledge_ingestion_service/indexing.py`:9-31
- **function** `OllamaQdrantIndexer._embed` — `services/knowledge_ingestion_service/src/knowledge_ingestion_service/indexing.py`:33-41
- **function** `OllamaQdrantIndexer.upsert` — `services/knowledge_ingestion_service/src/knowledge_ingestion_service/indexing.py`:43-49
- **function** `OllamaQdrantIndexer.reconcile_source` — `services/knowledge_ingestion_service/src/knowledge_ingestion_service/indexing.py`:51-94
- **class** `Chunk` — `services/knowledge_ingestion_service/src/knowledge_ingestion_service/models.py`:5-31
- **function** `Chunk.payload` — `services/knowledge_ingestion_service/src/knowledge_ingestion_service/models.py`:18-31
- **class** `IngestionReport` — `services/knowledge_ingestion_service/src/knowledge_ingestion_service/models.py`:34-52
- **function** `IngestionReport.to_dict` — `services/knowledge_ingestion_service/src/knowledge_ingestion_service/models.py`:51-52
- **function** `_excluded_path` — `services/knowledge_ingestion_service/src/knowledge_ingestion_service/pipeline.py`:75-80
- **function** `_excluded` — `services/knowledge_ingestion_service/src/knowledge_ingestion_service/pipeline.py`:83-93
- **function** `_discover` — `services/knowledge_ingestion_service/src/knowledge_ingestion_service/pipeline.py`:97-124
- **function** `_discover.onerror` — `services/knowledge_ingestion_service/src/knowledge_ingestion_service/pipeline.py`:103-107
- **function** `_chunks` — `services/knowledge_ingestion_service/src/knowledge_ingestion_service/pipeline.py`:128-202
- **function** `_artifact_id` — `services/knowledge_ingestion_service/src/knowledge_ingestion_service/pipeline.py`:205-208
- **function** `_make_stub_chunks` — `services/knowledge_ingestion_service/src/knowledge_ingestion_service/pipeline.py`:211-252
- **function** `_relative_path` — `services/knowledge_ingestion_service/src/knowledge_ingestion_service/pipeline.py`:255-263
- **class** `IngestionPipeline` — `services/knowledge_ingestion_service/src/knowledge_ingestion_service/pipeline.py`:266-593
- **function** `IngestionPipeline.__init__` — `services/knowledge_ingestion_service/src/knowledge_ingestion_service/pipeline.py`:267-282
- **function** `IngestionPipeline.run` — `services/knowledge_ingestion_service/src/knowledge_ingestion_service/pipeline.py`:284-288
- **function** `IngestionPipeline.run_for_current_agent` — `services/knowledge_ingestion_service/src/knowledge_ingestion_service/pipeline.py`:290-339
- **function** `IngestionPipeline._run` — `services/knowledge_ingestion_service/src/knowledge_ingestion_service/pipeline.py`:341-593
- **class** `FactionRetriever` — `services/knowledge_ingestion_service/src/knowledge_ingestion_service/retrieval.py`:1-66
- **function** `FactionRetriever.__init__` — `services/knowledge_ingestion_service/src/knowledge_ingestion_service/retrieval.py`:3-13
- **function** `FactionRetriever._embed` — `services/knowledge_ingestion_service/src/knowledge_ingestion_service/retrieval.py`:15-23
- **function** `FactionRetriever.search` — `services/knowledge_ingestion_service/src/knowledge_ingestion_service/retrieval.py`:25-66
- **function** `main` — `services/knowledge_ingestion_service/src/knowledge_ingestion_service/search_cli.py`:4-19
- **class** `BasicSummarizer` — `services/knowledge_ingestion_service/src/knowledge_ingestion_service/summarization.py`:3-7
- **function** `BasicSummarizer.summarize` — `services/knowledge_ingestion_service/src/knowledge_ingestion_service/summarization.py`:4-7
- **class** `OllamaSummarizer` — `services/knowledge_ingestion_service/src/knowledge_ingestion_service/summarization.py`:9-22
- **function** `OllamaSummarizer.__init__` — `services/knowledge_ingestion_service/src/knowledge_ingestion_service/summarization.py`:10-12
- **function** `OllamaSummarizer.summarize` — `services/knowledge_ingestion_service/src/knowledge_ingestion_service/summarization.py`:13-22
- **class** `StaticAgentProvider` — `services/knowledge_ingestion_service/tests/test_agent_ingestion.py`:7-12
- **function** `StaticAgentProvider.__init__` — `services/knowledge_ingestion_service/tests/test_agent_ingestion.py`:8-9
- **function** `StaticAgentProvider.resolve_current_agent_id` — `services/knowledge_ingestion_service/tests/test_agent_ingestion.py`:11-12
- **class** `StaticAgentRegistry` — `services/knowledge_ingestion_service/tests/test_agent_ingestion.py`:15-20
- **function** `StaticAgentRegistry.__init__` — `services/knowledge_ingestion_service/tests/test_agent_ingestion.py`:16-17
- **function** `StaticAgentRegistry.get_agent` — `services/knowledge_ingestion_service/tests/test_agent_ingestion.py`:19-20
- **class** `CaptureIndexer` — `services/knowledge_ingestion_service/tests/test_agent_ingestion.py`:23-32
- **function** `CaptureIndexer.__init__` — `services/knowledge_ingestion_service/tests/test_agent_ingestion.py`:24-25
- **function** `CaptureIndexer.upsert` — `services/knowledge_ingestion_service/tests/test_agent_ingestion.py`:27-29
- **function** `CaptureIndexer.reconcile_source` — `services/knowledge_ingestion_service/tests/test_agent_ingestion.py`:31-32
- **function** `test_agent_ingestion_records_provenance_on_content_and_stubs` — `services/knowledge_ingestion_service/tests/test_agent_ingestion.py`:35-59
- **function** `test_agent_ingestion_rejects_missing_provider` — `services/knowledge_ingestion_service/tests/test_agent_ingestion.py`:62-65
- **function** `test_agent_ingestion_rejects_missing_registry` — `services/knowledge_ingestion_service/tests/test_agent_ingestion.py`:68-71
- **function** `test_agent_ingestion_rejects_missing_authenticated_identity` — `services/knowledge_ingestion_service/tests/test_agent_ingestion.py`:74-80
- **function** `test_agent_ingestion_rejects_unknown_agent` — `services/knowledge_ingestion_service/tests/test_agent_ingestion.py`:83-89
- **function** `test_agent_ingestion_rejects_registry_identity_mismatch` — `services/knowledge_ingestion_service/tests/test_agent_ingestion.py`:92-103
- **function** `test_agent_registry_controls_faction_assignment` — `services/knowledge_ingestion_service/tests/test_agent_ingestion.py`:106-125
- **function** `test_manual_ingestion_keeps_agent_provenance_empty` — `services/knowledge_ingestion_service/tests/test_agent_ingestion.py`:128-136
- **function** `test_extractor_metadata_cannot_override_agent_provenance` — `services/knowledge_ingestion_service/tests/test_agent_ingestion.py`:139-155
- **function** `test_knife_faction_has_correct_domain` — `services/knowledge_ingestion_service/tests/test_factions.py`:4-9
- **function** `test_faction_and_source_namespaces_are_attached` — `services/knowledge_ingestion_service/tests/test_pipeline.py`:5-20
- **class** `test_faction_and_source_namespaces_are_attached.CaptureIndexer` — `services/knowledge_ingestion_service/tests/test_pipeline.py`:7-10
- **function** `test_faction_and_source_namespaces_are_attached.CaptureIndexer.__init__` — `services/knowledge_ingestion_service/tests/test_pipeline.py`:8-8
- **function** `test_faction_and_source_namespaces_are_attached.CaptureIndexer.upsert` — `services/knowledge_ingestion_service/tests/test_pipeline.py`:9-9
- **function** `test_faction_and_source_namespaces_are_attached.CaptureIndexer.reconcile_source` — `services/knowledge_ingestion_service/tests/test_pipeline.py`:10-10
- **function** `test_unknown_faction_rejected` — `services/knowledge_ingestion_service/tests/test_pipeline.py`:22-24
- **function** `test_secrets_excluded` — `services/knowledge_ingestion_service/tests/test_pipeline.py`:26-30
- **function** `test_csv_profile_and_report` — `services/knowledge_ingestion_service/tests/test_pipeline.py`:32-37
- **function** `test_failed_file_prevents_reconciliation` — `services/knowledge_ingestion_service/tests/test_pipeline.py`:39-48
- **class** `test_failed_file_prevents_reconciliation.TrackIndexer` — `services/knowledge_ingestion_service/tests/test_pipeline.py`:41-44
- **function** `test_failed_file_prevents_reconciliation.TrackIndexer.__init__` — `services/knowledge_ingestion_service/tests/test_pipeline.py`:42-42
- **function** `test_failed_file_prevents_reconciliation.TrackIndexer.upsert` — `services/knowledge_ingestion_service/tests/test_pipeline.py`:43-43
- **function** `test_failed_file_prevents_reconciliation.TrackIndexer.reconcile_source` — `services/knowledge_ingestion_service/tests/test_pipeline.py`:44-44
- **function** `test_chunk_ids_stay_stable_when_content_changes` — `services/knowledge_ingestion_service/tests/test_pipeline.py`:50-88
- **class** `test_chunk_ids_stay_stable_when_content_changes.CaptureIndexer` — `services/knowledge_ingestion_service/tests/test_pipeline.py`:51-62
- **function** `test_chunk_ids_stay_stable_when_content_changes.CaptureIndexer.__init__` — `services/knowledge_ingestion_service/tests/test_pipeline.py`:52-54
- **function** `test_chunk_ids_stay_stable_when_content_changes.CaptureIndexer.upsert` — `services/knowledge_ingestion_service/tests/test_pipeline.py`:56-58
- **function** `test_chunk_ids_stay_stable_when_content_changes.CaptureIndexer.reconcile_source` — `services/knowledge_ingestion_service/tests/test_pipeline.py`:60-62
- **function** `test_unsupported_file_creates_stub_and_allows_reconciliation` — `services/knowledge_ingestion_service/tests/test_pipeline.py`:91-117
- **class** `test_unsupported_file_creates_stub_and_allows_reconciliation.TrackIndexer` — `services/knowledge_ingestion_service/tests/test_pipeline.py`:94-105
- **function** `test_unsupported_file_creates_stub_and_allows_reconciliation.TrackIndexer.__init__` — `services/knowledge_ingestion_service/tests/test_pipeline.py`:95-97
- **function** `test_unsupported_file_creates_stub_and_allows_reconciliation.TrackIndexer.upsert` — `services/knowledge_ingestion_service/tests/test_pipeline.py`:99-101
- **function** `test_unsupported_file_creates_stub_and_allows_reconciliation.TrackIndexer.reconcile_source` — `services/knowledge_ingestion_service/tests/test_pipeline.py`:103-105
- **function** `test_unsupported_file_creates_metadata_stub` — `services/knowledge_ingestion_service/tests/test_pipeline.py`:119-153
- **class** `test_unsupported_file_creates_metadata_stub.CaptureIndexer` — `services/knowledge_ingestion_service/tests/test_pipeline.py`:122-133
- **function** `test_unsupported_file_creates_metadata_stub.CaptureIndexer.__init__` — `services/knowledge_ingestion_service/tests/test_pipeline.py`:123-125
- **function** `test_unsupported_file_creates_metadata_stub.CaptureIndexer.upsert` — `services/knowledge_ingestion_service/tests/test_pipeline.py`:127-129
- **function** `test_unsupported_file_creates_metadata_stub.CaptureIndexer.reconcile_source` — `services/knowledge_ingestion_service/tests/test_pipeline.py`:131-133
- **function** `test_failed_extraction_creates_stub_without_reconciliation` — `services/knowledge_ingestion_service/tests/test_pipeline.py`:156-186
- **class** `test_failed_extraction_creates_stub_without_reconciliation.CaptureIndexer` — `services/knowledge_ingestion_service/tests/test_pipeline.py`:162-173
- **function** `test_failed_extraction_creates_stub_without_reconciliation.CaptureIndexer.__init__` — `services/knowledge_ingestion_service/tests/test_pipeline.py`:163-165
- **function** `test_failed_extraction_creates_stub_without_reconciliation.CaptureIndexer.upsert` — `services/knowledge_ingestion_service/tests/test_pipeline.py`:167-169
- **function** `test_failed_extraction_creates_stub_without_reconciliation.CaptureIndexer.reconcile_source` — `services/knowledge_ingestion_service/tests/test_pipeline.py`:171-173
- **function** `test_stub_identity_is_stable` — `services/knowledge_ingestion_service/tests/test_pipeline.py`:189-211
- **function** `test_extract_utf16le_text_with_bom` — `services/knowledge_ingestion_service/tests/test_pipeline.py`:213-226
- **function** `test_extract_utf8_text_with_bom` — `services/knowledge_ingestion_service/tests/test_pipeline.py`:229-237
- **function** `test_extract_utf16_json_with_bom` — `services/knowledge_ingestion_service/tests/test_pipeline.py`:240-250
- **function** `test_malformed_json_still_fails` — `services/knowledge_ingestion_service/tests/test_pipeline.py`:253-260
- **function** `test_legacy_snapshot_and_backup_files_are_excluded` — `services/knowledge_ingestion_service/tests/test_pipeline.py`:263-300

## Dependency Overview

### `agents/forge/codebase_interpreter_agent/src/codebase_interpreter_agent/__init__.py`

- line 3: `scanner` (names: scan_project) — local, resolved_local

### `agents/forge/codebase_interpreter_agent/src/codebase_interpreter_agent/cli.py`

- line 2: `` (names: argparse) — standard_library, external
- line 3: `` (names: json) — standard_library, external
- line 4: `` (names: sys) — standard_library, external
- line 5: `pathlib` (names: Path) — standard_library, external
- line 7: `codebase_interpreter_agent.scanner` (names: scan_project) — local, resolved_local
- line 8: `codebase_interpreter_agent.dependency_resolver` (names: resolve_imports) — local, resolved_local
- line 9: `codebase_interpreter_agent.codebase_map` (names: build_codebase_map) — local, resolved_local
- line 10: `codebase_interpreter_agent.codebase_queries` (names: answer_codebase_question) — local, resolved_local

### `agents/forge/codebase_interpreter_agent/src/codebase_interpreter_agent/codebase_map.py`

- line 2: `__future__` (names: annotations) — standard_library, external
- line 4: `dataclasses` (names: asdict, dataclass, field) — standard_library, external
- line 6: `dependency_resolver` (names: DependencyReport) — local, resolved_local
- line 7: `scanner` (names: ScanReport) — local, resolved_local

### `agents/forge/codebase_interpreter_agent/src/codebase_interpreter_agent/codebase_queries.py`

- line 2: `__future__` (names: annotations) — standard_library, external

### `agents/forge/codebase_interpreter_agent/src/codebase_interpreter_agent/dependency_resolver.py`

- line 2: `__future__` (names: annotations) — standard_library, external
- line 4: `` (names: sys) — standard_library, external
- line 5: `collections` (names: defaultdict) — standard_library, external
- line 6: `dataclasses` (names: asdict, dataclass, field) — standard_library, external
- line 7: `pathlib` (names: PurePosixPath) — standard_library, external
- line 9: `scanner` (names: ScanReport) — local, resolved_local

### `agents/forge/codebase_interpreter_agent/src/codebase_interpreter_agent/scanner.py`

- line 1: `__future__` (names: annotations) — standard_library, external
- line 3: `` (names: ast) — standard_library, external
- line 4: `` (names: hashlib) — standard_library, external
- line 5: `` (names: os) — standard_library, external
- line 6: `` (names: time) — standard_library, external
- line 7: `dataclasses` (names: asdict, dataclass, field) — standard_library, external
- line 8: `pathlib` (names: Path) — standard_library, external

### `agents/forge/codebase_interpreter_agent/tests/test_codebase_map.py`

- line 2: `pathlib` (names: Path) — standard_library, external
- line 4: `codebase_interpreter_agent.scanner` (names: scan_project) — local, resolved_local
- line 5: `codebase_interpreter_agent.dependency_resolver` (names: resolve_imports) — local, resolved_local
- line 6: `codebase_interpreter_agent.codebase_map` (names: build_codebase_map) — local, resolved_local
- line 79: `` (names: json) — standard_library, external

### `agents/forge/codebase_interpreter_agent/tests/test_codebase_queries.py`

- line 2: `pathlib` (names: Path) — standard_library, external
- line 4: `codebase_interpreter_agent.scanner` (names: scan_project) — local, resolved_local
- line 5: `codebase_interpreter_agent.dependency_resolver` (names: resolve_imports) — local, resolved_local
- line 6: `codebase_interpreter_agent.codebase_map` (names: build_codebase_map) — local, resolved_local
- line 7: `codebase_interpreter_agent.codebase_queries` (names: find_dependents, find_symbol) — local, resolved_local

### `agents/forge/codebase_interpreter_agent/tests/test_codebase_question.py`

- line 2: `pathlib` (names: Path) — standard_library, external
- line 4: `codebase_interpreter_agent.scanner` (names: scan_project) — local, resolved_local
- line 5: `codebase_interpreter_agent.dependency_resolver` (names: resolve_imports) — local, resolved_local
- line 6: `codebase_interpreter_agent.codebase_map` (names: build_codebase_map) — local, resolved_local
- line 7: `codebase_interpreter_agent.codebase_queries` (names: answer_codebase_question) — local, resolved_local

### `agents/forge/codebase_interpreter_agent/tests/test_dependency_resolver.py`

- line 2: `pathlib` (names: Path) — standard_library, external
- line 4: `codebase_interpreter_agent.dependency_resolver` (names: resolve_imports) — local, resolved_local
- line 5: `codebase_interpreter_agent.scanner` (names: scan_project) — local, resolved_local

### `agents/forge/codebase_interpreter_agent/tests/test_scanner.py`

- line 1: `pathlib` (names: Path) — standard_library, external
- line 3: `codebase_interpreter_agent` (names: scan_project) — local, resolved_local
- line 63: `` (names: pytest) — likely_third_party, external

### `services/knowledge_ingestion_service/src/knowledge_ingestion_service/__init__.py`

- line 1: `pipeline` (names: IngestionPipeline) — local, resolved_local
- line 2: `factions` (names: FactionRegistry, Faction) — local, resolved_local
- line 3: `retrieval` (names: FactionRetriever) — local, resolved_local

### `services/knowledge_ingestion_service/src/knowledge_ingestion_service/agent_context.py`

- line 8: `dataclasses` (names: dataclass) — standard_library, external
- line 9: `typing` (names: Protocol) — standard_library, external

### `services/knowledge_ingestion_service/src/knowledge_ingestion_service/artifact_stubs.py`

- line 1: `pathlib` (names: Path) — standard_library, external

### `services/knowledge_ingestion_service/src/knowledge_ingestion_service/cli.py`

- line 1: `` (names: argparse, os) — standard_library, external
- line 2: `factions` (names: FactionRegistry) — local, resolved_local
- line 3: `pipeline` (names: IngestionPipeline) — local, resolved_local
- line 4: `indexing` (names: NoopIndexer, OllamaQdrantIndexer) — local, resolved_local

### `services/knowledge_ingestion_service/src/knowledge_ingestion_service/extractors.py`

- line 1: `` (names: csv, json, mimetypes) — standard_library, external
- line 2: `pathlib` (names: Path) — standard_library, external

### `services/knowledge_ingestion_service/src/knowledge_ingestion_service/factions.py`

- line 1: `dataclasses` (names: dataclass) — standard_library, external

### `services/knowledge_ingestion_service/src/knowledge_ingestion_service/indexing.py`

- line 10: `qdrant_client` (names: QdrantClient, models) — likely_third_party, external
- line 11: `` (names: urllib.request, json) — standard_library, external
- line 53: `qdrant_client.http` (names: models) — likely_third_party, external

### `services/knowledge_ingestion_service/src/knowledge_ingestion_service/models.py`

- line 1: `dataclasses` (names: dataclass, field, asdict) — standard_library, external
- line 2: `typing` (names: Any) — standard_library, external

### `services/knowledge_ingestion_service/src/knowledge_ingestion_service/pipeline.py`

- line 1: `` (names: hashlib) — standard_library, external
- line 2: `` (names: json) — standard_library, external
- line 3: `` (names: os) — standard_library, external
- line 4: `pathlib` (names: Path) — standard_library, external
- line 6: `factions` (names: FactionRegistry) — local, resolved_local
- line 7: `models` (names: Chunk, IngestionReport) — local, resolved_local
- line 8: `agent_context` (names: AgentIdentity) — local, resolved_local
- line 9: `extractors` (names: extract) — local, resolved_local
- line 10: `indexing` (names: NoopIndexer) — local, resolved_local
- line 11: `artifact_stubs` (names: make_artifact_stub) — local, resolved_local

### `services/knowledge_ingestion_service/src/knowledge_ingestion_service/retrieval.py`

- line 5: `factions` (names: FactionRegistry) — local, resolved_local
- line 6: `qdrant_client` (names: QdrantClient) — likely_third_party, external
- line 7: `` (names: urllib.request, json) — standard_library, external
- line 43: `qdrant_client.http` (names: models) — likely_third_party, external

### `services/knowledge_ingestion_service/src/knowledge_ingestion_service/search_cli.py`

- line 1: `` (names: argparse, os, json) — standard_library, external
- line 2: `retrieval` (names: FactionRetriever) — local, resolved_local

### `services/knowledge_ingestion_service/src/knowledge_ingestion_service/summarization.py`

- line 1: `` (names: os) — standard_library, external
- line 14: `` (names: requests) — likely_third_party, external

### `services/knowledge_ingestion_service/tests/test_agent_ingestion.py`

- line 1: `` (names: pytest) — likely_third_party, external
- line 3: `src.knowledge_ingestion_service.agent_context` (names: AgentIdentity) — local, resolved_local
- line 4: `src.knowledge_ingestion_service.pipeline` (names: IngestionPipeline) — local, resolved_local
- line 140: `src.knowledge_ingestion_service.models` (names: Chunk) — local, resolved_local

### `services/knowledge_ingestion_service/tests/test_factions.py`

- line 1: `src.knowledge_ingestion_service.factions` (names: FactionRegistry) — local, resolved_local

### `services/knowledge_ingestion_service/tests/test_pipeline.py`

- line 1: `` (names: json) — standard_library, external
- line 2: `` (names: pytest) — likely_third_party, external
- line 3: `src.knowledge_ingestion_service.pipeline` (names: IngestionPipeline) — local, resolved_local
- line 190: `src.knowledge_ingestion_service.pipeline` (names: _make_stub_chunks) — local, resolved_local
- line 191: `src.knowledge_ingestion_service.factions` (names: FactionRegistry) — local, resolved_local
- line 214: `src.knowledge_ingestion_service.extractors` (names: extract) — local, resolved_local
- line 230: `src.knowledge_ingestion_service.extractors` (names: extract) — local, resolved_local
- line 241: `src.knowledge_ingestion_service.extractors` (names: extract) — local, resolved_local
- line 254: `src.knowledge_ingestion_service.extractors` (names: extract) — local, resolved_local
- line 264: `src.knowledge_ingestion_service.pipeline` (names: _discover) — local, resolved_local

## Scan Issues

- Recorded errors: 0
- Recorded skipped items: 16

### Skipped Items

- .env: secret-like filename
- codebase_map.json: generated artifact or backup
- dependency_report.json: generated artifact or backup
- interpreter_review.txt: generated artifact or backup
- patch_scanner.py: generated artifact or backup
- scan_debug.log: generated artifact or backup
- scan_report.json: generated artifact or backup
- agents/forge/codebase_interpreter_agent/scan_debug.log: generated artifact or backup
- agents/forge/codebase_interpreter_agent/scan_report.json: generated artifact or backup
- agents/forge/codebase_interpreter_agent/src/codebase_interpreter_agent/scanner.py.bak: generated artifact or backup
- services/knowledge_ingestion_service/src/knowledge_ingestion_service/extractors.py.bak-encoding: generated artifact or backup
- services/knowledge_ingestion_service/src/knowledge_ingestion_service/pipeline.py.bak-corpus-hygiene: generated artifact or backup
- services/knowledge_ingestion_service/src/knowledge_ingestion_service/pipeline.py.bak-egg-info: generated artifact or backup
- services/knowledge_ingestion_service/src/knowledge_ingestion_service/pipeline.py.bak-manifest-exclusions: generated artifact or backup
- services/knowledge_ingestion_service/tests/test_pipeline.py.bak-corpus-hygiene: generated artifact or backup
- services/knowledge_ingestion_service/tests/test_pipeline.py.bak-egg-info: generated artifact or backup

## Interpretation Notes

- This report is generated from the saved static codebase map.
- Import edges describe detected import relationships, not proof that imported symbols execute at runtime.
- Dynamic imports, runtime-generated dependencies, and behavior not represented in the map may be absent.
- The report reflects the map's scan snapshot and may become stale when source files change.
