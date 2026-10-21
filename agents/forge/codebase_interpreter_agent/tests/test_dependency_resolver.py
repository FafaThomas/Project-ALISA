
from pathlib import Path

from src.codebase_interpreter_agent.dependency_resolver import resolve_imports
from src.codebase_interpreter_agent.scanner import scan_project


def test_resolves_absolute_local_imports(tmp_path: Path):
    package = tmp_path / "src" / "sample_pkg"
    package.mkdir(parents=True)

    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "models.py").write_text(
        "class Model: pass\n", encoding="utf-8"
    )
    (package / "service.py").write_text(
        "from sample_pkg.models import Model\n"
        "import sample_pkg.models\n",
        encoding="utf-8",
    )

    report = scan_project(tmp_path, verbose=False)
    dependencies = resolve_imports(report)

    local = [
        edge for edge in dependencies.edges
        if edge.status == "resolved_local"
    ]

    assert len(local) == 2
    assert all(
        edge.target_paths == ["src/sample_pkg/models.py"]
        for edge in local
    )


def test_resolves_relative_imports(tmp_path: Path):
    package = tmp_path / "src" / "sample_pkg"
    subpackage = package / "subpkg"
    subpackage.mkdir(parents=True)

    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "models.py").write_text(
        "class Model: pass\n", encoding="utf-8"
    )
    (subpackage / "__init__.py").write_text("", encoding="utf-8")
    (subpackage / "service.py").write_text(
        "from .. import models\n"
        "from ..models import Model\n",
        encoding="utf-8",
    )

    report = scan_project(tmp_path, verbose=False)
    dependencies = resolve_imports(report)

    local = [
        edge for edge in dependencies.edges
        if edge.status == "resolved_local"
    ]

    assert len(local) == 2
    assert all(
        edge.target_paths == ["src/sample_pkg/models.py"]
        for edge in local
    )


def test_classifies_standard_library_and_unresolved_relative_import(
    tmp_path: Path,
):
    package = tmp_path / "src" / "sample_pkg"
    package.mkdir(parents=True)

    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "module.py").write_text(
        "import json\n"
        "from .missing import Thing\n",
        encoding="utf-8",
    )

    report = scan_project(tmp_path, verbose=False)
    dependencies = resolve_imports(report)

    stdlib_edge = next(
        edge for edge in dependencies.edges
        if "json" in edge.imported_names
    )
    relative_edge = next(
        edge for edge in dependencies.edges
        if edge.imported_module == "missing"
    )

    assert stdlib_edge.classification == "standard_library"
    assert relative_edge.status == "unresolved"
    assert relative_edge.classification == "relative"


def test_resolves_src_prefixed_imports(tmp_path: Path):
    package = tmp_path / "src" / "sample_pkg"
    tests = tmp_path / "tests"
    package.mkdir(parents=True)
    tests.mkdir()

    (package / "__init__.py").write_text(
        "", encoding="utf-8"
    )
    (package / "pipeline.py").write_text(
        "class IngestionPipeline: pass\n",
        encoding="utf-8",
    )
    (tests / "test_pipeline.py").write_text(
        "from src.sample_pkg.pipeline import IngestionPipeline\n",
        encoding="utf-8",
    )

    report = scan_project(tmp_path, verbose=False)
    dependencies = resolve_imports(report)

    edge = next(
        edge for edge in dependencies.edges
        if edge.imported_module == "src.sample_pkg.pipeline"
    )

    assert edge.status == "resolved_local"
    assert edge.target_paths == ["src/sample_pkg/pipeline.py"]
