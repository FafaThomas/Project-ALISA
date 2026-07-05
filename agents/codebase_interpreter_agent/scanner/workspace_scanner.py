from pathlib import Path


IGNORE_DIRS = {
    ".git",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    "node_modules",
    "build",
    "dist",
    ".idea",
    ".vscode",
}


class WorkspaceScanner:

    def __init__(self, project_root: str | Path):

        self.project_root = Path(project_root)

    def discover_python_files(self) -> list[Path]:

        python_files = []

        for path in self.project_root.rglob("*.py"):

            if any(
                part in IGNORE_DIRS
                for part in path.parts
            ):
                continue

            python_files.append(path)

        return sorted(python_files)