"""Safe, read-only access to files inside an authorized workspace."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PureWindowsPath


DEFAULT_MAX_FILE_BYTES = 2 * 1024 * 1024


class WorkspaceError(Exception):
    """Base exception for workspace access errors."""


class WorkspacePathError(WorkspaceError):
    """Raised when a path is invalid or outside the workspace."""


class WorkspaceFileError(WorkspaceError):
    """Raised when a file cannot safely be read."""


@dataclass(frozen=True)
class FileSnapshot:
    """Contents and metadata captured when a file is read."""

    relative_path: str
    content: str
    size_bytes: int
    modified_ns: int


class Workspace:
    """Read-only file access constrained to a resolved workspace root."""

    def __init__(
        self,
        root: str | Path,
        *,
        max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    ) -> None:
        candidate = Path(root).expanduser()

        if max_file_bytes < 1:
            raise ValueError("max_file_bytes must be positive")
        if not candidate.exists():
            raise WorkspacePathError(f"Workspace does not exist: {candidate}")
        if not candidate.is_dir():
            raise WorkspacePathError(f"Workspace is not a directory: {candidate}")

        self.root = candidate.resolve(strict=True)
        self.max_file_bytes = max_file_bytes

    def resolve_path(self, relative_path: str | Path) -> Path:
        """Resolve an existing workspace-relative path without escaping root."""
        raw = str(relative_path)

        if not raw.strip():
            raise WorkspacePathError("Path must not be empty")

        supplied = Path(raw)
        if supplied.is_absolute() or supplied.drive or PureWindowsPath(raw).is_absolute() or PureWindowsPath(raw).drive:
            raise WorkspacePathError("Absolute paths are not allowed")

        # Reject traversal explicitly, even if normalization would bring the
        # final resolved path back inside the workspace.
        normalized_parts = raw.replace("\\", "/").split("/")
        if any(part == ".." for part in normalized_parts):
            raise WorkspacePathError("Parent traversal is not allowed")

        candidate = self.root / supplied

        try:
            resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise WorkspacePathError(
                f"Path does not resolve to an existing target: {raw}"
            ) from exc

        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise WorkspacePathError(
                "Resolved path escapes the authorized workspace"
            ) from exc

        return resolved

    def resolve_create_path(self, relative_path: str | Path) -> Path:
        """Validate a new file path, allowing missing parent directories.

        No directories are created here. Every existing ancestor is resolved
        so symlinked paths cannot redirect writes outside the workspace.
        """
        raw = str(relative_path).strip()
        if not raw:
            raise WorkspacePathError("Empty paths are not allowed")

        supplied = Path(raw)
        windows_path = PureWindowsPath(raw)
        if supplied.is_absolute() or supplied.drive or windows_path.is_absolute() or windows_path.drive:
            raise WorkspacePathError("Absolute paths are not allowed")
        normalized_parts = raw.replace("\\", "/").split("/")
        if any(part in {"", ".", ".."} for part in normalized_parts):
            raise WorkspacePathError("Parent traversal or invalid path component")

        candidate = self.root.joinpath(*normalized_parts)
        if candidate.exists() or candidate.is_symlink():
            raise WorkspacePathError("Target already exists")

        missing: list[str] = []
        ancestor = candidate.parent
        while not ancestor.exists() and not ancestor.is_symlink():
            missing.append(ancestor.name)
            parent = ancestor.parent
            if parent == ancestor:
                raise WorkspacePathError("No existing workspace ancestor")
            ancestor = parent

        try:
            resolved_ancestor = ancestor.resolve(strict=True)
            resolved_ancestor.relative_to(self.root)
        except (OSError, RuntimeError, ValueError) as exc:
            raise WorkspacePathError("Path escapes workspace or has unsafe ancestor") from exc

        if not resolved_ancestor.is_dir():
            raise WorkspacePathError("Parent path is not a directory")

        # Rebuild from the resolved ancestor to retain the authorized physical
        # path while leaving absent components uncreated.
        result = resolved_ancestor
        for part in reversed(missing):
            result = result / part
        return result / candidate.name

    def read_text(
        self,
        relative_path: str | Path,
        *,
        encoding: str = "utf-8",
    ) -> FileSnapshot:
        """Read a regular file with a size limit and freshness metadata."""
        resolved = self.resolve_path(relative_path)

        try:
            if not resolved.is_file():
                raise WorkspaceFileError(
                    f"Target is not a regular file: {relative_path}"
                )

            # Open first, then inspect the opened handle. This reduces the
            # risk of trusting stale path metadata before reading.
            with resolved.open("rb") as handle:
                import os

                stat = os.fstat(handle.fileno())

                if stat.st_size > self.max_file_bytes:
                    raise WorkspaceFileError(
                        f"File exceeds size limit of {self.max_file_bytes} bytes"
                    )

                data = handle.read(self.max_file_bytes + 1)

                if len(data) > self.max_file_bytes:
                    raise WorkspaceFileError(
                        f"File exceeds size limit of {self.max_file_bytes} bytes"
                    )

            # UTF-8 text files may include a leading BOM (common in files
            # created by Windows editors). Treat it as an encoding signature,
            # not as source text. Keep other requested encodings unchanged.
            decode_encoding = (
                "utf-8-sig"
                if encoding.lower().replace("_", "-") in {"utf-8", "utf8"}
                else encoding
            )
            try:
                content = data.decode(decode_encoding)
            except UnicodeDecodeError as exc:
                raise WorkspaceFileError(
                    f"File cannot be decoded as {encoding}: {relative_path}"
                ) from exc

            # Normalize Windows and legacy Mac line endings to Unix newlines.
            content = content.replace("\r\n", "\n").replace("\r", "\n")

            return FileSnapshot(
                relative_path=resolved.relative_to(self.root).as_posix(),
                content=content,
                size_bytes=len(data),
                modified_ns=stat.st_mtime_ns,
            )

        except WorkspaceError:
            raise
        except OSError as exc:
            raise WorkspaceFileError(
                f"Could not read file {relative_path}: {exc}"
            ) from exc