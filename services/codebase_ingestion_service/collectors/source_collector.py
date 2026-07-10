from pathlib import Path
from datetime import datetime

from models.source import (
    SourceDocument,
    SourceCollection,
)

from utils.hash import sha256_file


INTERPRETER_MAP = {

    ".py": "python",
    ".cs": "dotnet",
    ".cpp": "cpp",
    ".c": "c",
    ".h": "cpp",
    ".hpp": "cpp",

    ".js": "node",
    ".ts": "node",
    ".tsx": "react",
    ".jsx": "react",

    ".java": "java",
    ".kt": "kotlin",
    ".swift": "swift",
    ".go": "go",
    ".rs": "rust",
    ".php": "php",

    ".html": "browser",
    ".css": "browser",

    ".sql": "sql",

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

class SourceCollector:

    def collect(self, workspace) -> SourceCollection:

        project = Path(workspace.path)

        files = []

        languages = {}

        for file in project.rglob("*"):

            if not file.is_file():
                continue

            relative_path = file.relative_to(project)

            if any(
                part in IGNORE_DIRECTORIES
                for part in relative_path.parts
            ):
                continue

            suffix = file.suffix.lower()

            if suffix not in INTERPRETER_MAP:
                continue

            interpreter = INTERPRETER_MAP[suffix]

            language = suffix.lstrip(".")

            content = file.read_text(

                encoding="utf-8",

                errors="ignore"

            )

            relative_path = file.relative_to(project).as_posix()

            files.append(

                SourceDocument(

                    relative_path=relative_path,

                    extension=suffix,

                    parser="generic",

                    interpreter=interpreter,

                    language=language,

                    size=file.stat().st_size,

                    modified=datetime.fromtimestamp(
                        file.stat().st_mtime
                    ),

                    hash=sha256_file(file),

                    raw_source=content,

                )

            )

            languages[language] = languages.get(language, 0) + 1

        return SourceCollection(

            total_files=len(files),

            languages=languages,

            files=files,

        )