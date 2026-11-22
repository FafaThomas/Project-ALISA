from coding_agent.parsers.python_parser import PythonParser


def test_empty_source_is_valid_and_has_no_symbols_or_imports():
    parsed = PythonParser().parse("empty.py", "")
    assert parsed.symbols == ()
    assert parsed.imports == ()


def test_unicode_identifiers_and_nested_qualified_names_are_preserved():
    source = "class Café:\n    def привет(self):\n        def nested():\n            return 1\n        return nested()\n"
    parsed = PythonParser().parse("unicode.py", source)
    names = {symbol.qualified_name for symbol in parsed.symbols}
    assert "Café" in names
    assert "Café.привет" in names
    assert "Café.привет.nested" in names


def test_comments_do_not_create_symbols_or_imports():
    parsed = PythonParser().parse("comments.py", "# def fake():\n# import fake_module\n")
    assert parsed.symbols == ()
    assert parsed.imports == ()
