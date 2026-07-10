from pathlib import Path

from models.manifest import (
    ManifestCollection,
    ManifestDocument,
)


MANIFEST_TYPES = {

    "README.md": "readme",

    "requirements.txt": "requirements",

    "pyproject.toml": "python_project",

    "package.json": "node_project",

    "package-lock.json": "node_lock",

    "Dockerfile": "dockerfile",

    "docker-compose.yml": "docker_compose",

    "docker-compose.yaml": "docker_compose",

    ".env": "environment",

    ".env.example": "environment",

    ".gitignore": "gitignore",

}


IGNORE_DIRECTORIES = {

    ".git",

    ".venv",

    "__pycache__",

    "node_modules",

    ".idea",

    ".vscode",

    "bin",

    "obj",

    "build",

    "dist",

    "storage",

    "snapshots",

}


IGNORE_FILES = {

    "source_collection.json",

    "project_context.json",

    "result.txt",

    "tree.txt",

}


class ManifestCollector:

    def collect(self, workspace) -> ManifestCollection:

        project = Path(workspace.path)

        documents = []

        for file in project.rglob("*"):

            if not file.is_file():
                continue

            relative_path = file.relative_to(project)

            if any(
                part in IGNORE_DIRECTORIES
                for part in relative_path.parts
            ):
                continue

            if file.name in IGNORE_FILES:
                continue

            if file.name not in MANIFEST_TYPES:
                continue

            content = file.read_text(
                encoding="utf-8",
                errors="ignore",
            )

            documents.append(

                ManifestDocument(

                    relative_path=relative_path.as_posix(),

                    document_type=MANIFEST_TYPES[file.name],

                    content=content,

                )

            )

        return ManifestCollection(

            total_documents=len(documents),

            documents=documents,

        )