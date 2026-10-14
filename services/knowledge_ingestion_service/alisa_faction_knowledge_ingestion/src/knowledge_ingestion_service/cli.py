import argparse, os
from .pipeline import IngestionPipeline
from .indexing import NoopIndexer, OllamaQdrantIndexer

def main():
    parser = argparse.ArgumentParser(description="Ingest artifacts into one ALISA faction collection.")
    parser.add_argument("root")
    parser.add_argument("--faction", required=True, help="Registered faction ID, e.g. forge or pentagram")
    parser.add_argument("--index", action="store_true")
    parser.add_argument("--report", default="ingestion-report.json")
    args = parser.parse_args()
    indexer = NoopIndexer()
    if args.index:
        from .factions import FactionRegistry
        faction = FactionRegistry().get(args.faction)
        indexer = OllamaQdrantIndexer(
            faction.collection_name,
            qdrant_url=os.getenv("ALISA_QDRANT_URL", "http://127.0.0.1:6333"),
            ollama_url=os.getenv("ALISA_OLLAMA_URL", "http://localhost:11434"),
            embed_model=os.getenv("ALISA_EMBED_MODEL", "nomic-embed-text:latest"),
        )
    report = IngestionPipeline(indexer=indexer).run(args.root, args.faction, args.report)
    print(f"Faction: {report.faction_id} | Collection: {report.collection_name}")
    print(f"Complete: {report.complete} | discovered={report.discovered} extracted={report.extracted} skipped={report.skipped} failed={report.failed}")
    print(f"Indexed chunks: {report.indexed_chunks} | stale points removed: {report.stale_points_removed}")
    print(f"Report: {args.report}")
    if report.failed:
        raise SystemExit(1)

if __name__ == "__main__":
    main()
