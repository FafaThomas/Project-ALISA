from pathlib import Path

import pytest

from coding_agent.workspace import (
    Workspace,
    WorkspaceFileError,
    WorkspacePathError,
)


def test_reads_file_inside_workspace(tmp_path):
    (tmp_path / "hello.py").write_text("VALUE = 42\n", encoding="utf-8")

    workspace = Workspace(tmp_path)
    snapshot = workspace.read_text("hello.py")

    assert snapshot.content == "VALUE = 42\n"
    assert snapshot.relative_path == "hello.py"
    assert snapshot.size_bytes == (tmp_path / "hello.py").stat().st_size
    assert snapshot.modified_ns > 0


def test_rejects_parent_traversal(tmp_path):
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("secret", encoding="utf-8")

    workspace = Workspace(tmp_path)

    with pytest.raises(WorkspacePathError):
        workspace.resolve_path("../outside.txt")


def test_rejects_absolute_paths(tmp_path):
    workspace = Workspace(tmp_path)

    with pytest.raises(WorkspacePathError, match="Absolute"):
        workspace.resolve_path(str(tmp_path / "secret.txt"))


def test_rejects_missing_paths(tmp_path):
    workspace = Workspace(tmp_path)

    with pytest.raises(WorkspacePathError):
        workspace.resolve_path("missing.txt")


def test_rejects_directory_as_file(tmp_path):
    (tmp_path / "folder").mkdir()
    workspace = Workspace(tmp_path)

    with pytest.raises(WorkspaceFileError, match="regular file"):
        workspace.read_text("folder")


def test_enforces_file_size_limit(tmp_path):
    (tmp_path / "large.txt").write_text("123456789", encoding="utf-8")
    workspace = Workspace(tmp_path, max_file_bytes=5)

    with pytest.raises(WorkspaceFileError, match="size limit"):
        workspace.read_text("large.txt")


def test_reports_invalid_text_encoding(tmp_path):
    (tmp_path / "binary.bin").write_bytes(b"\xff\xfe\xfa")
    workspace = Workspace(tmp_path)

    with pytest.raises(WorkspaceFileError, match="decoded"):
        workspace.read_text("binary.bin")


def test_rejects_symlink_escape_when_supported(tmp_path):
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()

    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")

    link = workspace_root / "escape.txt"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("Symlink creation is not permitted in this environment")

    workspace = Workspace(workspace_root)

    with pytest.raises(WorkspacePathError, match="escapes"):
        workspace.read_text("escape.txt")


def test_rejects_nonexistent_workspace(tmp_path):
    with pytest.raises(WorkspacePathError, match="does not exist"):
        Workspace(tmp_path / "missing")

@pytest.mark.parametrize(
    "unsafe_path",
    [
        "../outside.txt",
        "nested/../../outside.txt",
        r"nested\..\..\outside.txt",
        r"..\outside.txt",
    ],
)
def test_rejects_traversal_variants(tmp_path, unsafe_path):
    workspace = Workspace(tmp_path)

    with pytest.raises(WorkspacePathError, match="traversal"):
        workspace.resolve_path(unsafe_path)


def test_rejects_windows_drive_absolute_path(tmp_path):
    workspace = Workspace(tmp_path)

    with pytest.raises(WorkspacePathError, match="Absolute"):
        workspace.resolve_path(r"C:\outside\secret.txt")


def test_rejects_workspace_root_as_file(tmp_path):
    workspace = Workspace(tmp_path)

    with pytest.raises(WorkspaceFileError, match="regular file"):
        workspace.read_text(".")
