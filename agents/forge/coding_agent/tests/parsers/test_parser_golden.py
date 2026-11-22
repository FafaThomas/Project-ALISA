import json
from pathlib import Path

import pytest

from coding_agent.parsers.defaults import build_default_registry

ROOT = Path(__file__).parent

@pytest.mark.parametrize("language", ["javascript", "typescript", "sql", "go", "rust"])
def test_parser_matches_golden_symbol_contract(language):
    expected = json.loads((ROOT / "golden" / f"{language}.json").read_text(encoding="utf-8"))
    fixture_dir = ROOT / "fixtures" / language
    source_path = next(fixture_dir.iterdir())
    source = source_path.read_text(encoding="utf-8")
    parser = build_default_registry().get_parser(source_path.name)
    parsed = parser.parse(source_path.name, source)
    actual = {(symbol.kind, symbol.name) for symbol in parsed.symbols}
    assert expected["language"] == parser.language
    assert set(map(tuple, expected["symbols"])).issubset(actual)
