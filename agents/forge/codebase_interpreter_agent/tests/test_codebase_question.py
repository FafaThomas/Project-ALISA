
from pathlib import Path

from codebase_interpreter_agent.scanner import scan_project
from codebase_interpreter_agent.dependency_resolver import resolve_imports
from codebase_interpreter_agent.codebase_map import build_codebase_map
from codebase_interpreter_agent.codebase_queries import (
    answer_codebase_question,
)


def _make_map(tmp_path: Path) -> dict:
    package = tmp_path / "src" / "sample_pkg"
    package.mkdir(parents=True)

    (package / "__init__.py").write_text(
        "", encoding="utf-8"
    )
    (package / "pipeline.py").write_text(
        "class IngestionPipeline:\n"
        "    pass\n",
        encoding="utf-8",
    )
    (package / "cli.py").write_text(
        "from sample_pkg.pipeline import IngestionPipeline\n"
        "from sample_pkg.pipeline import SomeHelper\n",
        encoding="utf-8",
    )

    scan = scan_project(tmp_path, verbose=False)
    dependencies = resolve_imports(scan)

    return build_codebase_map(scan, dependencies).to_dict()


def test_answers_where_symbol_is_defined(tmp_path: Path):
    codebase_map = _make_map(tmp_path)

    result = answer_codebase_question(
        codebase_map,
        "Where is IngestionPipeline defined?",
    )

    assert result["status"] == "answered"
    assert "src/sample_pkg/pipeline.py" in result["answer"]
    assert result["evidence"][0]["line"] == 1


def test_answers_which_files_depend_on_symbol(tmp_path: Path):
    codebase_map = _make_map(tmp_path)

    result = answer_codebase_question(
        codebase_map,
        "Which files depend on IngestionPipeline?",
    )

    assert result["status"] == "answered"
    assert len(result["evidence"]) == 1
    assert (
        result["evidence"][0]["source_path"]
        == "src/sample_pkg/cli.py"
    )


def test_unknown_symbol_does_not_invent_evidence(tmp_path: Path):
    codebase_map = _make_map(tmp_path)

    result = answer_codebase_question(
        codebase_map,
        "Where is ImaginaryAgent defined?",
    )

    assert result["status"] == "no_match"
    assert result["evidence"] == []
