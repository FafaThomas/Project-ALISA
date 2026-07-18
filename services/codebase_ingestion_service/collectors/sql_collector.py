from pathlib import Path
from datetime import datetime

from sql.models.sql import (
    SQLDocument,
    SQLCollection,
)

from utils.hash import sha256_file
from utils.constants import EXCLUDED_PATHS


SQL_EXTENSIONS = {

    ".sql",

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


class SQLCollector:

    def collect(self, workspace) -> SQLCollection:

        project = Path(workspace.path)

        documents = []

        for file in project.rglob("*"):

            relative = file.relative_to(workspace.path).as_posix()

            if any(
                relative == path or relative.startswith(path + "/")
                for path in EXCLUDED_PATHS
            ):
                continue

            try:

                if not file.is_file():
                    continue

            except OSError:
                continue

            relative_path = file.relative_to(project)

            if any(
                part in IGNORE_DIRECTORIES
                for part in relative_path.parts
            ):
                continue

            suffix = file.suffix.lower()

            if suffix not in SQL_EXTENSIONS:
                continue

            content = file.read_text(
                encoding="utf-8",
                errors="ignore",
            )

            documents.append(

                SQLDocument(

                    relative_path=relative_path.as_posix(),

                    extension=suffix,

                    size=file.stat().st_size,

                    modified=datetime.fromtimestamp(
                        file.stat().st_mtime
                    ),

                    hash=sha256_file(file),

                    raw_source=content,

                )

            )

        return SQLCollection(

            total_documents=len(documents),

            documents=documents,

        )