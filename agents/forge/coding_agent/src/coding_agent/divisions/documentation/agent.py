"""Documentation and Maintenance (division): this unit as a Python object.

    from coding_agent.unit_entry import load_unit_agent
    unit = load_unit_agent("documentation")
    report = unit.run(request, invocation)

Its role, triggers and model are in unit.md. Shared behaviour lives in
coding_agent/core; anything specific to this unit goes in the class below.
"""

from coding_agent.unit_entry import UnitAgent


class DocumentationAndMaintenance(UnitAgent):
    unit_file = __file__
