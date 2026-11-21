
from pathlib import Path

import pytest

from coding_agent.code_index import CodeIndexer
from coding_agent.context_engine import ContextEngine
from coding_agent.models import CodingTask
from coding_agent.workspace import Workspace, WorkspaceError


def make_task(root, description="Update authentication middleware"):
    return CodingTask(
        task_id="context-001",
        description=description,
        workspace=str(root),
        constraints=("Preserve existing interfaces",),
        acceptance_criteria=("Existing tests must pass",),
    )


def test_context_includes_project_metadata_and_source_files(tmp_path):
    (tmp_path / "README.md").write_text(
        "# Example Project\nA Python application.",
        encoding="utf-8",
    )
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = 'example'",
        encoding="utf-8",
    )

    source = tmp_path / "src" / "authentication"
    source.mkdir(parents=True)
    (source / "middleware.py").write_text(
        "class AuthenticationMiddleware:\n    pass\n",
        encoding="utf-8",
    )

    engine = ContextEngine(Workspace(tmp_path))
    package = engine.build(make_task(tmp_path))

    paths = package.source_paths

    assert "README.md" in paths
    assert "pyproject.toml" in paths
    assert "src/authentication/middleware.py" in paths


def test_context_prioritizes_task_relevant_paths(tmp_path):
    (tmp_path / "README.md").write_text(
        "Project overview",
        encoding="utf-8",
    )

    unrelated = tmp_path / "billing"
    unrelated.mkdir()
    (unrelated / "invoice.py").write_text(
        "class Invoice:\n    pass\n",
        encoding="utf-8",
    )

    relevant = tmp_path / "authentication"
    relevant.mkdir()
    (relevant / "middleware.py").write_text(
        "class AuthenticationMiddleware:\n    pass\n",
        encoding="utf-8",
    )

    engine = ContextEngine(
        Workspace(tmp_path),
        max_files=2,
    )

    package = engine.build(
        make_task(tmp_path, "Update authentication middleware")
    )

    assert "README.md" in package.source_paths
    assert "authentication/middleware.py" in package.source_paths
    assert "billing/invoice.py" not in package.source_paths


def test_context_ignores_generated_directories(tmp_path):
    source = tmp_path / "src"
    source.mkdir()

    generated = tmp_path / "node_modules"
    generated.mkdir()

    cache = tmp_path / "__pycache__"
    cache.mkdir()

    (source / "main.py").write_text(
        "print('hello')",
        encoding="utf-8",
    )
    (generated / "dependency.js").write_text(
        "module.exports = {}",
        encoding="utf-8",
    )
    (cache / "compiled.py").write_text(
        "compiled",
        encoding="utf-8",
    )

    package = ContextEngine(Workspace(tmp_path)).build(
        make_task(tmp_path)
    )

    assert "src/main.py" in package.source_paths
    assert all("node_modules" not in path for path in package.source_paths)
    assert all("__pycache__" not in path for path in package.source_paths)


def test_context_excludes_sensitive_environment_files(tmp_path):
    (tmp_path / ".env").write_text(
        "API_KEY=super-secret-value",
        encoding="utf-8",
    )
    (tmp_path / ".env.example").write_text(
        "API_KEY=replace-me",
        encoding="utf-8",
    )

    package = ContextEngine(Workspace(tmp_path)).build(
        make_task(tmp_path)
    )

    assert ".env" not in package.source_paths
    assert ".env.example" in package.source_paths
    assert "super-secret-value" not in package.render()


def test_context_enforces_max_file_count(tmp_path):
    for index in range(5):
        (tmp_path / f"module_{index}.py").write_text(
            f"VALUE = {index}",
            encoding="utf-8",
        )

    engine = ContextEngine(
        Workspace(tmp_path),
        max_files=2,
    )

    package = engine.build(make_task(tmp_path))

    assert len(package.files) == 2
    assert package.omitted_files == 3
    assert package.truncated is True


def test_context_enforces_per_file_character_limit(tmp_path):
    (tmp_path / "large.py").write_text(
        "A" * 1000,
        encoding="utf-8",
    )

    engine = ContextEngine(
        Workspace(tmp_path),
        max_file_chars=100,
    )

    package = engine.build(make_task(tmp_path))

    assert len(package.files) == 1
    assert len(package.files[0].content) == 100
    assert package.files[0].truncated is True


def test_context_enforces_total_character_limit(tmp_path):
    for index in range(3):
        (tmp_path / f"module_{index}.py").write_text(
            "A" * 100,
            encoding="utf-8",
        )

    engine = ContextEngine(
        Workspace(tmp_path),
        max_file_chars=100,
        max_context_chars=150,
    )

    package = engine.build(make_task(tmp_path))

    assert package.total_characters <= 150
    assert package.truncated is True


def test_context_skips_files_that_cannot_be_decoded(tmp_path):
    (tmp_path / "valid.py").write_text(
        "VALUE = 1",
        encoding="utf-8",
    )
    (tmp_path / "invalid.py").write_bytes(
        b"\xff\xfe\xfa"
    )

    package = ContextEngine(Workspace(tmp_path)).build(
        make_task(tmp_path)
    )

    assert "valid.py" in package.source_paths
    assert "invalid.py" not in package.source_paths


def test_context_rejects_a_different_workspace(tmp_path):
    authorized = tmp_path / "authorized"
    unauthorized = tmp_path / "unauthorized"
    authorized.mkdir()
    unauthorized.mkdir()

    engine = ContextEngine(Workspace(authorized))

    with pytest.raises(WorkspaceError, match="does not match"):
        engine.build(make_task(unauthorized))


def test_context_render_includes_source_attribution(tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    (source / "main.py").write_text(
        "def main():\n    return 42",
        encoding="utf-8",
    )

    package = ContextEngine(Workspace(tmp_path)).build(
        make_task(tmp_path)
    )

    rendered = package.render()

    assert "PROJECT CONTEXT" in rendered
    assert "DIRECTORY STRUCTURE:" in rendered
    assert "SOURCE FILES:" in rendered
    assert "===== src/main.py =====" in rendered
    assert "return 42" in rendered


def test_context_validates_configuration(tmp_path):
    workspace = Workspace(tmp_path)

    with pytest.raises(ValueError):
        ContextEngine(workspace, max_files=0)

    with pytest.raises(ValueError):
        ContextEngine(workspace, max_file_chars=0)

    with pytest.raises(ValueError):
        ContextEngine(workspace, max_context_chars=0)

    with pytest.raises(ValueError):
        ContextEngine(workspace, max_candidates=0)

def test_context_expands_dependencies_and_dependents(tmp_path):
    package = tmp_path / "src" / "myapp"
    package.mkdir(parents=True)

    tests = tmp_path / "tests"
    tests.mkdir()

    (package / "__init__.py").write_text("", encoding="utf-8")

    (package / "authentication.py").write_text(
        "from myapp.token_service import TokenService\n"
        "class AuthenticationService:\n"
        "    pass\n",
        encoding="utf-8",
    )

    (package / "token_service.py").write_text(
        "class TokenService:\n"
        "    pass\n",
        encoding="utf-8",
    )

    (package / "api.py").write_text(
        "from myapp.authentication import AuthenticationService\n",
        encoding="utf-8",
    )

    (tests / "test_authentication.py").write_text(
        "from myapp.authentication import AuthenticationService\n",
        encoding="utf-8",
    )

    engine = ContextEngine(Workspace(tmp_path))

    package_result = engine.build(
        make_task(tmp_path, "Update authentication")
    )

    paths = set(package_result.source_paths)

    assert "src/myapp/authentication.py" in paths
    assert "src/myapp/token_service.py" in paths
    assert "src/myapp/api.py" in paths
    assert "tests/test_authentication.py" in paths

def test_context_includes_bounded_code_structure(tmp_path):
    source = tmp_path / "src" / "myapp"
    source.mkdir(parents=True)

    (source / "__init__.py").write_text("", encoding="utf-8")

    (source / "authentication.py").write_text(
        "class AuthenticationService:\n"
        "    def authenticate(self, token):\n"
        "        return True\n",
        encoding="utf-8",
    )

    (source / "token_service.py").write_text(
        "class TokenService:\n"
        "    pass\n",
        encoding="utf-8",
    )

    (source / "authentication.py").write_text(
        "from myapp.token_service import TokenService\n"
        "class AuthenticationService:\n"
        "    def authenticate(self, token):\n"
        "        return True\n",
        encoding="utf-8",
    )

    result = ContextEngine(Workspace(tmp_path)).build(
        make_task(tmp_path, "Update authentication")
    )

    assert "CODE STRUCTURE:" in result.render()
    assert "AuthenticationService" in result.code_structure
    assert "INTERNAL" not in result.code_structure

def test_context_limits_code_structure_size(tmp_path):
    source = tmp_path / "authentication.py"

    source.write_text(
        "\n".join(
            f"def function_{number}(): pass"
            for number in range(100)
        ),
        encoding="utf-8",
    )

    result = ContextEngine(
        Workspace(tmp_path),
        max_structure_chars=200,
    ).build(
        make_task(tmp_path, "Update authentication")
    )

    assert len(result.code_structure) <= (
        200 + len("\n[Code structure truncated.]")
    )
    assert result.truncated is True

def test_context_rejects_indexer_from_different_workspace(tmp_path):
    authorized = tmp_path / "authorized"
    unauthorized = tmp_path / "unauthorized"

    authorized.mkdir()
    unauthorized.mkdir()

    indexer = CodeIndexer(Workspace(unauthorized))

    with pytest.raises(
        WorkspaceError,
        match="CodeIndexer workspace",
    ):
        ContextEngine(
            Workspace(authorized),
            code_indexer=indexer,
        )
