import hashlib, json
from pathlib import Path
from .factions import FactionRegistry
from .models import Artifact, Chunk, IngestionReport
from .extractors import extract
from .indexing import NoopIndexer

SECRET_NAMES = {".env", ".env.local", ".env.production", "credentials.json", "secrets.json"}
SECRET_SUFFIXES = {".pem", ".key", ".p12", ".pfx"}

def _excluded(path):
    return path.name.lower() in SECRET_NAMES or path.name.lower().startswith(".env.") or path.suffix.lower() in SECRET_SUFFIXES or any(
        part.lower() in {".git", ".venv", "venv", "__pycache__", "node_modules"} for part in path.parts
    )

def _chunks(artifact, source_id, size=2400, overlap=250):
    text = artifact.text.strip()
    chunks, start, ordinal = [], 0, 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            boundary = max(text.rfind("\n", start + size//2, end), text.rfind(" ", start + size//2, end))
            if boundary > start:
                end = boundary
        body = text[start:end].strip()
        if body:
            cid = hashlib.sha256(f"{artifact.artifact_id}:{artifact.content_hash}:{ordinal}:{body}".encode()).hexdigest()[:32]
            chunks.append(Chunk(cid, artifact.artifact_id, artifact.faction_id, artifact.collection_name,
                source_id, artifact.relative_path, artifact.content_hash, ordinal, body,
                {"title": Path(artifact.relative_path).name, "summary": artifact.summary,
                 "extractor": artifact.extractor, "media_type": artifact.media_type, "start_char": start, "end_char": end}))
            ordinal += 1
        if end >= len(text): break
        start = max(start + 1, end - overlap)
    return chunks

class IngestionPipeline:
    def __init__(self, registry=None, indexer=None, summarizer=None):
        self.registry = registry or FactionRegistry()
        self.indexer = indexer or NoopIndexer()
        self.summarizer = summarizer or (lambda text, path: text[:1200] + ("\n[excerpt truncated]" if len(text) > 1200 else ""))

    def run(self, root, faction_id, report_path=None, reconcile=True, source_id=None):
        faction = self.registry.get(faction_id)
        root = Path(root).resolve()
        if not root.is_dir():
            raise ValueError(f"Input root is not a directory: {root}")
        source_id = source_id or hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:24]
        report = IngestionReport(faction.faction_id, faction.collection_name, str(root))
        report.warnings.append(f"Reconciliation namespace: {source_id}")
        candidates = [p for p in root.rglob("*") if p.is_file() and not _excluded(p)]
        report.discovered = len(candidates)
        current_artifacts, chunks, failed = {}, [], False
        for path in candidates:
            rel = path.relative_to(root).as_posix()
            aid = hashlib.sha256(f"{faction.faction_id}:{source_id}:{rel}".encode()).hexdigest()[:32]
            try:
                content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
                text, extractor, media_type, metadata = extract(path)
                artifact = Artifact(aid, faction.faction_id, faction.collection_name, rel, content_hash,
                                    extractor, media_type, text, self.summarizer(text, rel), metadata)
                parts = _chunks(artifact, source_id)
                current_artifacts[aid] = content_hash
                chunks.extend(parts)
                report.extracted += 1
                report.files.append({"path": rel, "status": "extracted", "artifact_id": aid, "chunks": len(parts)})
            except NotImplementedError as exc:
                report.skipped += 1
                report.files.append({"path": rel, "status": "unsupported", "message": str(exc)})
            except Exception as exc:
                failed = True
                report.failed += 1
                report.files.append({"path": rel, "status": "failed", "message": f"{type(exc).__name__}: {exc}"})
        try:
            report.indexed_chunks = self.indexer.upsert(chunks)
        except Exception as exc:
            failed = True
            report.failed += 1
            report.warnings.append(f"Index upsert failed; reconciliation skipped: {type(exc).__name__}: {exc}")
        report.complete = not failed
        if reconcile and report.complete:
            try:
                report.stale_points_removed = self.indexer.reconcile(source_id, current_artifacts)
            except Exception as exc:
                report.warnings.append(f"Reconciliation failed: {type(exc).__name__}: {exc}")
        elif reconcile:
            report.warnings.append("Reconciliation skipped because the run had failures.")
        if report_path:
            Path(report_path).write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
        return report
