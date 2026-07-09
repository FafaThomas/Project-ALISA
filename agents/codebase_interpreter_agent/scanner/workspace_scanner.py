from pathlib import Path


IGNORE_DIRS = {
    ".git",
    ".venv",
    "__pycache__",
    "node_modules",
    "build",
    "dist",
}


class WorkspaceScanner:

    def scan(self, project_path: str | Path):

        project_path = Path(project_path)

        python_files = []

        for file in project_path.rglob("*.py"):

            if any(part in IGNORE_DIRS for part in file.parts):
                continue

            python_files.append(file)

        return sorted(python_files)