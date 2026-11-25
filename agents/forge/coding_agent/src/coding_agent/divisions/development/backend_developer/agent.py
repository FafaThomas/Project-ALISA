"""Backend Developer (specialist, Development division): this unit as a Python object.

    from coding_agent.unit_entry import load_unit_agent
    unit = load_unit_agent("development/backend_developer")
    report = unit.run(request, invocation)

Its role, triggers and model are in unit.md. Shared behaviour lives in
coding_agent/core; anything specific to this unit goes in the class below.
"""

from coding_agent.unit_entry import UnitAgent


class BackendDeveloper(UnitAgent):
    unit_file = __file__

    # Optional: override any of these to change how this unit works.
    #
    # def extra_context(self, query, workspace_root):
    #     """Text added to this unit's context before its model is called."""
    #     return None
    #
    # def review(self, files):
    #     """Deterministic findings over (path, content) pairs:
    #     [{"severity": "medium", "file": path, "issue": "...", "suggestion": "..."}]"""
    #     return []
    #
    # def after_output(self, output, action):
    #     """Inspect or adjust this unit's output.
    #     action: consult | coordinate | build | repair | review"""
    #     return output
