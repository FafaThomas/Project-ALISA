from coding_agent.parsers.base import ImportRecord, ParsedSource, ReferenceRecord, Symbol


def test_rich_symbol_fields_are_backward_compatible():
    symbol = Symbol("run", "Engine.run", "method", "engine.py", 4, 8)
    assert symbol.signature is None
    assert symbol.return_type is None
    assert symbol.modifiers == ()
    assert symbol.decorators == ()
    assert symbol.is_exported is False
    assert symbol.is_async is False
    assert symbol.role is None


def test_import_records_preserve_language_specific_import_semantics():
    record = ImportRecord("./types", ("User",), 0, 3, kind="import", is_type_only=True)
    assert record.is_type_only is True
    assert record.kind == "import"


def test_parsed_source_can_carry_unresolved_references():
    reference = ReferenceRecord("Customer", "jsx_component", 12)
    parsed = ParsedSource((), (), (reference,))
    assert parsed.references == (reference,)
