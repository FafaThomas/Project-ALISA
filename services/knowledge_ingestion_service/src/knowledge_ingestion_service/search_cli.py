import argparse, os, json
from .retrieval import FactionRetriever

def main():
    parser = argparse.ArgumentParser(description="Search one or more ALISA faction collections.")
    parser.add_argument("query")
    parser.add_argument("--faction", help="One faction ID")
    parser.add_argument("--factions", help="Comma-separated faction IDs for explicit cross-faction retrieval")
    parser.add_argument("--source-id", default=None)
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()
    factions = [args.faction] if args.faction else [x.strip() for x in (args.factions or "").split(",") if x.strip()]
    if not factions:
        parser.error("Provide --faction or --factions")
    retriever = FactionRetriever(
        qdrant_url=os.getenv("ALISA_QDRANT_URL", "http://127.0.0.1:6333"),
        ollama_url=os.getenv("ALISA_OLLAMA_URL", "http://localhost:11434"),
        embed_model=os.getenv("ALISA_EMBED_MODEL", "nomic-embed-text:latest"))
    print(json.dumps(retriever.search(args.query, factions, args.top_k, args.source_id), indent=2, ensure_ascii=False))

if __name__ == "__main__":
    main()
