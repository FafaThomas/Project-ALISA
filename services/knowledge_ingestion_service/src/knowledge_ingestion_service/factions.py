from dataclasses import dataclass

@dataclass(frozen=True)
class Faction:
    faction_id: str
    collection_name: str
    description: str = ""

class FactionRegistry:
    """Explicit allowlist: never derive collection names from untrusted input."""
    def __init__(self, factions=None, prefix="alisa_faction_"):
        factions = factions or [
            Faction("forge", f"{prefix}forge", "Engineering, modeling, coding, networking, hacking, and robotics"),
            Faction("pentagram", f"{prefix}pentagram", "Chemistry, physics, mathematics, biology, and medicine"),
            Faction("corpus", f"{prefix}corpus", "Languages and cultures"),
            Faction("knife", f"{prefix}knife", "Food, agriculture, hunting, and cooking"),
        ]
        self._factions = {f.faction_id: f for f in factions}

    def get(self, faction_id):
        try:
            return self._factions[faction_id.strip().lower()]
        except (KeyError, AttributeError):
            raise ValueError(f"Unknown faction_id: {faction_id!r}. Register it before ingestion/retrieval.")

    def all(self):
        return tuple(self._factions.values())
