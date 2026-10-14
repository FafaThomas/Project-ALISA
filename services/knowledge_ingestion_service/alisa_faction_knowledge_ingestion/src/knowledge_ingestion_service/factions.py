from dataclasses import dataclass

@dataclass(frozen=True)
class Faction:
    faction_id: str
    collection_name: str
    description: str = ""

class FactionRegistry:
    """Explicit allowlist: never derive collection names from untrusted input."""
    def __init__(self, factions=None):
        factions = factions or [
            Faction("forge", "alisa_faction_forge", "Engineering and technical disciplines"),
            Faction("pentagram", "alisa_faction_pentagram", "Scientific and medical disciplines"),
            Faction("corpus", "alisa_faction_corpus", "Languages and cultures"),
            Faction("knife", "alisa_faction_knife", "Security, adversarial analysis, and tactical operations"),
        ]
        self._factions = {f.faction_id: f for f in factions}

    def get(self, faction_id: str) -> Faction:
        try:
            return self._factions[faction_id.strip().lower()]
        except (KeyError, AttributeError):
            raise ValueError(f"Unknown faction_id: {faction_id!r}. Register the faction before ingestion.")

    def all(self):
        return tuple(self._factions.values())
