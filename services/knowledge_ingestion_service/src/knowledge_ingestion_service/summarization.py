import os

class BasicSummarizer:
    def summarize(self, text: str, title: str, media_type: str) -> str:
        compact = " ".join(text.split())
        if not compact: return f"{title}: no extractable text."
        return f"{title} ({media_type}). Extracted content excerpt: {compact[:700]}" + ("…" if len(compact) > 700 else "")

class OllamaSummarizer:
    def __init__(self, base_url=None, model=None):
        self.base_url = (base_url or os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")).rstrip("/")
        self.model = model or os.getenv("OLLAMA_SUMMARY_MODEL", "qwen2.5:14b")
    def summarize(self, text: str, title: str, media_type: str) -> str:
        import requests
        r = requests.post(self.base_url + "/api/generate", timeout=90, json={
            "model": self.model, "stream": False,
            "prompt": ("Summarize this artifact for technical retrieval. Identify purpose, concepts, key names/values, and limitations. "
                       "Do not invent facts.\nTitle: " + title + "\nMedia type: " + media_type +
                       "\nContent:\n" + text[:24000])
        })
        r.raise_for_status()
        return r.json()["response"].strip()
