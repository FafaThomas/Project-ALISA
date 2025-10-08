# core/memory.py
from typing import List, Dict

class SessionMemory:
    """Tracks short-term conversation context."""
    def __init__(self, max_turns: int = 5):
        self.turns: List[Dict[str, str]] = []
        self.max_turns = max_turns

    def add_turn(self, user: str, ai: str):
        self.turns.append({"user": user, "ai": ai})
        if len(self.turns) > self.max_turns:
            self.turns.pop(0)

    def get_context(self) -> str:
        """Combine recent conversation turns into a text context."""
        return "\n".join(
            [f"User: {t['user']}\nALISA: {t['ai']}" for t in self.turns]
        )

    def clear(self):
        self.turns = []

class Memory:
    def __init__(self):
        self.history = []

    def add_message(self, role, content):
        self.history.append({"role": role, "content": content})
        # Keep it short for now — 10 last turns only
        if len(self.history) > 10:
            self.history = self.history[-10:]

    def get_context(self):
        return "\n".join(
            [f"{msg['role'].capitalize()}: {msg['content']}" for msg in self.history]
        )
