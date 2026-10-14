class FactionRetriever:
    """Semantic retrieval over explicitly selected faction collections."""
    def __init__(self, registry=None, qdrant_url="http://127.0.0.1:6333",
                 ollama_url="http://localhost:11434", embed_model="nomic-embed-text:latest"):
        from .factions import FactionRegistry
        from qdrant_client import QdrantClient
        import urllib.request, json
        self.registry = registry or FactionRegistry()
        self.client = QdrantClient(url=qdrant_url)
        self.ollama_url = ollama_url.rstrip("/")
        self.embed_model = embed_model
        self.urllib = urllib.request
        self.json = json

    def _embed(self, text):
        body = self.json.dumps({"model": self.embed_model, "input": text}).encode()
        req = self.urllib.Request(f"{self.ollama_url}/api/embed", data=body, headers={"Content-Type": "application/json"})
        with self.urllib.urlopen(req, timeout=90) as response:
            result = self.json.loads(response.read().decode())
        vectors = result.get("embeddings") or []
        if not vectors:
            raise RuntimeError("Ollama returned no embedding")
        return vectors[0]

    def search(self, query, faction_ids, top_k=5, source_id=None):
        if not query.strip():
            raise ValueError("Query cannot be empty")
        if top_k < 1 or top_k > 100:
            raise ValueError("top_k must be between 1 and 100")
        vector = self._embed(query)
        results = []
        for faction_id in faction_ids:
            faction = self.registry.get(faction_id)
            try:
                self.client.get_collection(faction.collection_name)
            except Exception as exc:
                msg = str(exc).lower()
                if "not found" in msg or "doesn't exist" in msg:
                    continue
                raise
            query_filter = None
            if source_id:
                from qdrant_client.http import models as http_models
                query_filter = http_models.Filter(must=[
                    http_models.FieldCondition(key="source_id", match=http_models.MatchValue(value=source_id))
                ])
            response = self.client.query_points(
                collection_name=faction.collection_name, query=vector,
                query_filter=query_filter, limit=top_k, with_payload=True)
            for point in response.points:
                payload = point.payload or {}
                results.append({
                    "score": point.score,
                    "faction_id": faction.faction_id,
                    "collection_name": faction.collection_name,
                    "source_id": payload.get("source_id"),
                    "artifact_id": payload.get("artifact_id"),
                    "relative_path": payload.get("relative_path"),
                    "content_hash": payload.get("content_hash"),
                    "ordinal": payload.get("ordinal"),
                    "text": payload.get("text"),
                    "summary": payload.get("summary"),
                    "metadata": payload,
                })
        results.sort(key=lambda item: item["score"] if item["score"] is not None else -1, reverse=True)
        return results[:top_k]
