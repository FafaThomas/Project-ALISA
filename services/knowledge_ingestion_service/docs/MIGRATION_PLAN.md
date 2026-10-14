# Migration plan: retire `codebase_ingestion_service`

1. Add this service at `services/knowledge_ingestion_service` alongside the old service.
2. Run unit tests and extraction-only smoke tests against a COPY of the existing tiny test repository.
3. Test Ollama summarization and embeddings in the actual ALISA environment.
4. Index into a NEW Qdrant test collection and verify dimensions, payloads, retrieval, and reconciliation.
5. Update `agents/codebase_ingestion_agent/tools/ingestion_tools.py` to use the new pipeline/report contract. Do not assume its current `create_project(...)` call remains compatible.
6. Migrate dependency graph, call graph, and project-context generation into optional codebase enrichment if still useful.
7. Fix collection naming in one place; the old agent config suffix and service default previously differed.
8. Avoid `importlib.import_module("service")` because the generic name can collide with another module.
9. Only after the agent smoke test passes should `services/codebase_ingestion_service` be deleted and all imports, docs, and config references updated.

## Important limits in this starter
- Qdrant stale-point reconciliation is per artifact and currently scrolls at most 10,000 points per artifact. Add pagination before huge artifacts.
- Reconciliation is not an atomic whole-run transaction.
- CAD/3D, PDF/Office, MATLAB `.mat`, and Simulink `.slx` remain unsupported until extractors or conversion adapters are implemented.
- The default summary is a deterministic excerpt, not semantic LLM summarization. Enable `--ollama-summaries` for actual Ollama summaries.
