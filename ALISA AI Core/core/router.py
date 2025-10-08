from core.llm import ask_model
from core.agents.embedded_software_engineer import EmbeddedSoftwareEngineerAgent
from core.agents.cloud_engineer import CloudEngineerAgent
from core.agents.electronics_engineer import ElectronicsEngineerAgent
from core.agents.pentester import PentesterAgent
from core.agents.python_dev import PythonDevAgent
from core.agents.relationship_expert import RelationshipExpertAgent
from core.agents.philosophy_expert import PhilosophyExpertAgent
from core.agents.flirty_companion import FlirtyCompanionAgent
from core.agents.vfd_expert import VFDExpertAgent


# Initialize all agents
agents = {
    "embedded": EmbeddedSoftwareEngineerAgent(),
    "cloud": CloudEngineerAgent(),
    "electronics": ElectronicsEngineerAgent(),
    "pentester": PentesterAgent(),
    "python": PythonDevAgent(),
    "relationship": RelationshipExpertAgent(),
    "philosophy": PhilosophyExpertAgent(),
    "flirty": FlirtyCompanionAgent(),
    "vfd_expert": VFDExpertAgent(),
}


def detect_agent(user_input: str) -> str:
    """Decide which agent should handle the message."""
    msg = user_input.lower()

    if any(word in msg for word in ["server", "aws", "cloud", "docker"]):
        return "cloud"
    elif any(word in msg for word in ["embedded", "firmware", "avr", "microcontroller"]):
        return "embedded"
    elif any(word in msg for word in ["circuit", "voltage", "resistor", "pcb"]):
        return "electronics"
    elif any(word in msg for word in ["vfd", "variable frequency drive", "inverter", "motor speed control", "ac drive"]):
        return "vfd_expert"
    elif any(word in msg for word in ["hack", "exploit", "vulnerability", "pentest"]):
        return "pentester"
    elif any(word in msg for word in ["python", "script", "django", "api"]):
        return "python"
    elif any(word in msg for word in ["relationship", "love", "girlfriend", "boyfriend"]):
        return "relationship"
    elif any(word in msg for word in ["life", "meaning", "ethics", "philosophy"]):
        return "philosophy"
    elif any(word in msg for word in ["hi", "hello", "good morning", "good evening", "babe", "sweetie"]):
        return "flirty"
    else:
        return "flirty"  # fallback agent


def route_message(user_input: str, context: str = "", model_func=ask_model):
    """Main router that determines which agent to use and gets the response."""
    selected_agent_key = detect_agent(user_input)
    agent = agents[selected_agent_key]

    reply = agent.respond(user_input, context, model_func)
    return "ALISA", reply
