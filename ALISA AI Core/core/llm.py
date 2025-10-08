# core/llm.py
import requests
from .memory import SessionMemory

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL_NAME = "mistral:7b-instruct-q4_K_M"

# Initialize a global short-term memory
memory = SessionMemory(max_turns=5)

def ask_model(user_message: str) -> str:
    """
    Send a prompt to Ollama, maintaining conversational context.
    """
    try:
        # Get conversation history
        context = memory.get_context()

        # Build a conversational prompt
        prompt = f"""
You are ALISA — an intelligent, kind, and emotionally-aware AI assistant 
created by Thomas David Eclarino. You are helpful, conversational, and natural in tone. 
Respond conversationally, not like a code generator, unless explicitly asked 
for technical help.

Here is the ongoing conversation:

{context}

User: {user_message}
ALISA:"""

        body = {"model": MODEL_NAME, "prompt": prompt, "stream": False}
        response = requests.post(OLLAMA_URL, json=body, timeout=120)
        response.raise_for_status()
        data = response.json()
        ai_reply = data.get("response", "").strip()

        # Update memory
        memory.add_turn(user_message, ai_reply)

        return ai_reply

    except Exception as e:
        return f"[LLM Error] {str(e)}"
