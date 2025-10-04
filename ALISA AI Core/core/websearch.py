# core/websearch.py
import requests

def search_web(query: str) -> str:
    """Placeholder function for web or API-based searches."""
    try:
        url = "https://api.duckduckgo.com"
        params = {"q": query, "format": "json", "no_redirect": 1, "no_html": 1}
        r = requests.get(url, params=params, timeout=10)
        data = r.json()
        abstract = data.get("AbstractText", "")
        related = data.get("RelatedTopics", [])
        if abstract:
            return abstract
        elif related:
            return related[0].get("Text", "No direct summary found.")
        else:
            return "No relevant information found."
    except Exception as e:
        return f"[WebSearch Error] {str(e)}"
