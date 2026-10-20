from pathlib import Path

from codebase_interpreter_agent import scan_project


def test_scans_python_symbols_and_imports(tmp_path: Path):
    (tmp_path / "main.py").write_text(
        "import os\n"
        "from pathlib import Path\n\n"
        "class Device:\n"
        "    def start(self):\n"
        "        return True\n\n"
        "async def launch():\n"
        "    pass\n",
        encoding="utf-8",
    )

    report = scan_project(tmp_path)

    symbols = {(s.kind, s.qualified_name, s.line) for s in report.symbols}
    assert ("class", "Device", 4) in symbols
    assert ("function", "Device.start", 5) in symbols
    assert ("async_function", "launch", 8) in symbols

    assert len(report.imports) == 2
    assert report.files[0].status == "analyzed"


def test_excludes_secrets_and_generated_directories(tmp_path: Path):
    (tmp_path / ".env").write_text("TOKEN=secret", encoding="utf-8")
    (tmp_path / "credentials.json").write_text("{}", encoding="utf-8")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "ignored.py").write_text("x = 1", encoding="utf-8")
    (tmp_path / "main.py").write_text("x = 1", encoding="utf-8")

    report = scan_project(tmp_path)
    paths = {record.path for record in report.files}

    assert paths == {"main.py"}


def test_parse_error_is_reported_without_aborting_scan(tmp_path: Path):
    (tmp_path / "broken.py").write_text("def broken(:\n", encoding="utf-8")
    (tmp_path / "valid.py").write_text("def works():\n    return 1\n", encoding="utf-8")

    report = scan_project(tmp_path)

    assert any(f.path == "broken.py" and f.status == "parse_error" for f in report.files)
    assert any(s.name == "works" for s in report.symbols)
    assert report.errors


def test_large_file_is_skipped(tmp_path: Path):
    (tmp_path / "large.py").write_text("x = 123456789", encoding="utf-8")

    report = scan_project(tmp_path, max_file_bytes=4)

    assert report.files[0].status == "skipped_too_large"
    assert not report.symbols


def test_invalid_root_and_size_limit_are_rejected(tmp_path: Path):
    import pytest

    with pytest.raises(ValueError):
        scan_project(tmp_path / "missing")

    with pytest.raises(ValueError):
        scan_project(tmp_path, max_file_bytes=0)

def test_excludes_packaging_metadata_and_analysis_artifacts(
    tmp_path: Path,
):
    package = tmp_path / "src" / "sample_pkg"
    metadata = (
        tmp_path
        / "src"
        / "sample_pkg.egg-info"
    )

    package.mkdir(parents=True)
    metadata.mkdir(parents=True)

    (package / "__init__.py").write_text(
        "", encoding="utf-8"
    )
    (metadata / "PKG-INFO").write_text(
        "Generated package metadata",
        encoding="utf-8",
    )
    (tmp_path / "dependency_report.json").write_text(
        "{}", encoding="utf-8"
    )
    (tmp_path / "codebase_map.json").write_text(
        "{}", encoding="utf-8"
    )
    (tmp_path / "interpreter_review.txt").write_text(
        "Temporary review snapshot",
        encoding="utf-8",
    )

    report = scan_project(tmp_path, verbose=False)
    paths = {record.path.replace("\\", "/") for record in report.files}

    assert "src/sample_pkg/__init__.py" in paths
    assert not any(".egg-info/" in path for path in paths)
    assert "dependency_report.json" not in paths
    assert "codebase_map.json" not in paths
    assert "interpreter_review.txt" not in paths
