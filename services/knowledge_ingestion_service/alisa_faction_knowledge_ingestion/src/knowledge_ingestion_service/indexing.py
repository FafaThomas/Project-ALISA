class NoopIndexer:
    def upsert(self, chunks):
        return 0
    def reconcile(self, source_id, current_artifacts):
        return 0

class OllamaQdrantIndexer:
    """Index to exactly one faction collection per instance."""
    def __init__(self, collection_name, qdrant_url="http://127.0.0.1:6333",
                 ollama_url="http://localhost:11434", embed_model="nomic-embed-text:latest"):
        from qdrant_client import QdrantClient, models
        import urllib.request, json
        self.client = QdrantClient(url=qdrant_url)
        self.models = models
        self.collection_name = collection_name
        self.ollama_url = ollama_url.rstrip("/")
        self.embed_model = embed_model
        self.urllib = urllib.request
        self.json = json
        vector = self._embed("dimension check")
        try:
            info = self.client.get_collection(collection_name)
            conf = info.config.params.vectors
            size = next(iter(conf.values())).size if isinstance(conf, dict) else conf.size
            if size != len(vector):
                raise RuntimeError(f"Vector dimension mismatch for {collection_name}: {size} != {len(vector)}")
        except Exception as exc:
            msg = str(exc).lower()
            if "not found" not in msg and "doesn't exist" not in msg:
                raise
            self.client.create_collection(collection_name=collection_name,
                vectors_config=models.VectorParams(size=len(vector), distance=models.Distance.COSINE))

    def _embed(self, text):
        body = self.json.dumps({"model": self.embed_model, "input": text}).encode()
        req = self.urllib.Request(f"{self.ollama_url}/api/embed", data=body, headers={"Content-Type": "application/json"})
        with self.urllib.urlopen(req, timeout=90) as response:
            result = self.json.loads(response.read().decode())
        vectors = result.get("embeddings") or []
        if not vectors:
            raise RuntimeError("Ollama returned no embedding")
        return vectors[0]

    def upsert(self, chunks):
        points = []
        for c in chunks:
            points.append(self.models.PointStruct(id=c.chunk_id, vector=self._embed(c.text), payload=c.payload()))
        if points:
            self.client.upsert(collection_name=self.collection_name, points=points, wait=True)
        return len(points)

    def reconcile(self, source_id, current_artifacts):
        from qdrant_client.http import models as http_models
        offset, stale = None, []
        while True:
            points, offset = self.client.scroll(collection_name=self.collection_name, offset=offset,
                limit=256, with_payload=["artifact_id"], with_vectors=False)
            stale.extend(p.id for p in points if (p.payload or {}).get("source_id") == source_id and ((p.payload or {}).get("artifact_id") not in current_artifacts or (p.payload or {}).get("content_hash") != current_artifacts.get((p.payload or {}).get("artifact_id"))))
            if offset is None:
                break
        for start in range(0, len(stale), 256):
            self.client.delete(collection_name=self.collection_name,
                points_selector=http_models.PointIdsList(points=stale[start:start+256]), wait=True)
        return len(stale)
