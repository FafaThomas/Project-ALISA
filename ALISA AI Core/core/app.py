# core/app.py
from fastapi import FastAPI, Request
from core.memory import SessionMemory
from core.router import route_message
from core.llm import ask_model

app = FastAPI(title="ALISA AI Core", version="1.0")

# Session memory for short context retention
memory = SessionMemory(max_turns=5)

@app.get("/")
def home():
    return {"status": "ALISA AI Core Online", "version": "1.0"}

@app.post("/api/query")
async def query(req: Request):
    data = await req.json()
    msg = data.get("message", "")
    context = memory.get_context()

    # Determine which agent handles this
    agent_name, reply = route_message(msg, context, ask_model)

    memory.add_turn(msg, reply)

    return {
        "agent": agent_name,
        "reply": reply,
    }
