# core/router.py
import re
from core.agents.cloud_engineer import CloudEngineerAgent
from core.agents.electronics_engineer import ElectronicsEngineerAgent
from core.agents.embedded_software_engineer import EmbeddedSoftwareEngineerAgent
from core.agents.pentester import PentesterAgent
from core.agents.python_dev import PythonDevAgent

AGENTS = {
    "cloud": CloudEngineerAgent(),
    "electronics": ElectronicsEngineerAgent(),
    "embedded": EmbeddedSoftwareEngineerAgent(),
    "pentest": PentesterAgent(),
    "python": PythonDevAgent(),
}

def detect_agent(message: str) -> str:
    """Simple keyword routing — can later evolve to classifier model."""
    msg = message.lower()
    if any(k in msg for k in ["aws", "gcp", "cloud", "docker", "kubernetes"]):
        return "cloud"
    elif any(k in msg for k in ["pcb", "circuit", "resistor", "voltage", "op amp"]):
        return "electronics"
    elif any(k in msg for k in ["firmware", "embedded", "microcontroller", "c code"]):
        return "embedded"
    elif any(k in msg for k in ["vulnerability", "penetration", "pentest", "nmap", "exploit"]):
        return "pentest"
    elif any(k in msg for k in ["python", "script", "automation", "api", "django", "fastapi"]):
        return "python"
    else:
        return "python"  # fallback

def route_message(message: str, context: str, model_func):
    """Route a message to the appropriate agent and return its reply."""
    agent_key = detect_agent(message)
    agent = AGENTS.get(agent_key)
    reply = agent.respond(message, context, model_func)
    return agent.name, reply
