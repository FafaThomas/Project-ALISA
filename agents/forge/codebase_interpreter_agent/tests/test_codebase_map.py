
from pathlib import Path

from src.codebase_interpreter_agent.scanner import scan_project
from src.codebase_interpreter_agent.dependency_resolver import resolve_imports
from src.codebase_interpreter_agent.codebase_map import build_codebase_map


def test_builds_codebase_map_with_symbols_and_dependencies(
    tmp_path: Path,
):
    package = tmp_path / "src" / "sample_pkg"
    package.mkdir(parents=True)

    (package / "__init__.py").write_text(
        "", encoding="utf-8"
    )
    (package / "models.py").write_text(
        "class Model:\n"
        "    pass\n",
        encoding="utf-8",
    )
    (package / "service.py").write_text(
        "from sample_pkg.models import Model\n"
        "\n"
        "def create_model():\n"
        "    return Model()\n",
        encoding="utf-8",
    )

    scan_report = scan_project(tmp_path, verbose=False)
    dependency_report = resolve_imports(scan_report)

    codebase_map = build_codebase_map(
        scan_report,
        dependency_report,
    )
    result = codebase_map.to_dict()

    assert result["summary"]["file_count"] == 3
    assert result["summary"]["symbol_count"] == 2
    assert result["summary"]["import_edge_count"] == 1

    service = next(
        node for node in result["nodes"]
        if node["path"] == "src/sample_pkg/service.py"
    )

    assert any(
        symbol["name"] == "create_model"
        for symbol in service["symbols"]
    )

    edge = result["edges"][0]

    assert edge["source_path"] == "src/sample_pkg/service.py"
    assert edge["status"] == "resolved_local"
    assert edge["target_paths"] == [
        "src/sample_pkg/models.py"
    ]


def test_codebase_map_serializes_to_json(tmp_path: Path):
    package = tmp_path / "src" / "sample_pkg"
    package.mkdir(parents=True)

    (package / "__init__.py").write_text(
        "", encoding="utf-8"
    )

    scan_report = scan_project(tmp_path, verbose=False)
    dependency_report = resolve_imports(scan_report)

    result = build_codebase_map(
        scan_report,
        dependency_report,
    ).to_dict()

    import json

    serialized = json.dumps(result)
    restored = json.loads(serialized)

    assert restored["summary"]["file_count"] == 1
    assert restored["nodes"][0]["path"] == (
        "src/sample_pkg/__init__.py"
    )
