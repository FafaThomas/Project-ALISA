from src.knowledge_ingestion_service.factions import FactionRegistry


def test_knife_faction_has_correct_domain():
    knife = FactionRegistry().get("knife")

    assert knife.faction_id == "knife"
    assert knife.collection_name == "alisa_faction_knife"
    assert knife.description == "Food, agriculture, hunting, and cooking"
