import argparse, os
from .factions import FactionRegistry
from .pipeline import IngestionPipeline
from .indexing import NoopIndexer, OllamaQdrantIndexer

def main():
    parser = argparse.ArgumentParser(description="Ingest artifacts into one ALISA faction collection.")
    parser.add_argument("root")
    parser.add_argument("--faction", required=True)
    parser.add_argument("--source-id", default=None, help="Stable namespace for this source; recommended for production")
    parser.add_argument("--index", action="store_true")
    parser.add_argument("--report", default="ingestion-report.json")
    args = parser.parse_args()
    faction = FactionRegistry().get(args.faction)
    indexer = NoopIndexer()
    if args.index:
        indexer = OllamaQdrantIndexer(faction.collection_name,
            qdrant_url=os.getenv("ALISA_QDRANT_URL", "http://127.0.0.1:6333"),
            ollama_url=os.getenv("ALISA_OLLAMA_URL", "http://localhost:11434"),
            embed_model=os.getenv("ALISA_EMBED_MODEL", "nomic-embed-text:latest"))
    report = IngestionPipeline(indexer=indexer).run(args.root, args.faction, args.source_id, args.report)
    print(f"Faction: {report.faction_id} | Collection: {report.collection_name} | Source: {report.source_id}")
    print(f"Complete: {report.complete} | discovered={report.discovered} extracted={report.extracted} skipped={report.skipped} failed={report.failed}")
    print(f"Indexed chunks: {report.indexed_chunks} | stale points removed: {report.stale_points_removed}")
    print(f"Report: {args.report}")
    if report.failed:
        raise SystemExit(1)

if __name__ == "__main__":
    main()
