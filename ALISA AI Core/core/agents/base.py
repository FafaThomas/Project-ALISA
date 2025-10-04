# core/agents/base.py

class BaseAgent:
    def __init__(self, name: str, role: str, system_prompt: str):
        self.name = name
        self.role = role
        self.system_prompt = system_prompt

    def build_prompt(self, user_prompt: str, context: str = "") -> str:
        return f"{self.system_prompt}\n{context}\nUser: {user_prompt}\n{self.name}:"

    def respond(self, user_prompt: str, context: str, model_func):
        """Generic agent responder using a provided model function (e.g., ask_model)."""
        full_prompt = self.build_prompt(user_prompt, context)
        reply = model_func(full_prompt)
        return reply.strip()
