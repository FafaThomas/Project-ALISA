"""Serve the Technical Architect (specialist) as its own HTTP service.

    python api.py --allow-root D:/work --port 8801

Same options as `coding-agent serve`, already scoped to this unit. To have
parent units call this service, set `endpoint: http://<host>:<port>` in unit.md.
"""

from coding_agent.unit_entry import serve_unit

if __name__ == "__main__":
    raise SystemExit(serve_unit(__file__))
