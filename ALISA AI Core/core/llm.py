# core/llm.py
import requests

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL_NAME = "mistral:7b-instruct-q4_K_M"

def ask_model(prompt: str) -> str:
    """Send a prompt to Ollama and return the model’s response."""
    try:
        body = {"model": MODEL_NAME, "prompt": prompt, "stream": False}
        response = requests.post(OLLAMA_URL, json=body, timeout=120)
        response.raise_for_status()
        data = response.json()
        return data.get("response", "").strip()
    except Exception as e:
        return f"[LLM Error] {str(e)}"
