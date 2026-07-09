from collections import Counter
from pathlib import Path
from datetime import datetime

from models.source import SourceCollection, SourceDocument
from utils.hashing import file_hash


SUPPORTED_EXTENSIONS = {

    ".py",
    ".cs",
    ".java",
    ".js",
    ".ts",
    ".cpp",
    ".c",
    ".hpp",
    ".h",
    ".go",
    ".rs",
    ".kt",
    ".swift"

}


LANGUAGE_MAP = {

    ".py": "python",
    ".cs": "csharp",
    ".java": "java",
    ".js": "javascript",
    ".ts": "typescript",
    ".cpp": "cpp",
    ".c": "c",
    ".hpp": "cpp",
    ".h": "c",
    ".go": "go",
    ".rs": "rust",
    ".kt": "kotlin",
    ".swift": "swift"

}

PARSER_MAP = {

    "python": "python_ast",

    "csharp": "roslyn",

    "java": "javaparser",

    "javascript": "tree_sitter",

    "typescript": "tree_sitter",

    "cpp": "tree_sitter",

    "c": "tree_sitter",

    "go": "tree_sitter",

    "rust": "tree_sitter",

    "kotlin": "tree_sitter",

    "swift": "tree_sitter",

}

INTERPRETER_MAP = {

    "python": "python",

    "csharp": "csharp",

    "java": "java",

    "javascript": "javascript",

    "typescript": "typescript",

    "cpp": "cpp",

    "c": "c",

    "go": "go",

    "rust": "rust",

    "kotlin": "kotlin",

    "swift": "swift",

}


IGNORE_DIRS = {

    ".git",

    ".venv",

    "venv",

    "__pycache__",

    "node_modules",

    "site-packages",

}


class SourceCollector:

    def collect(self, workspace: Path) -> SourceCollection:

        files = []

        language_counter = Counter()

        for file in workspace.rglob("*"):

            if not file.is_file():

                continue

            if any(
                part.lower() in IGNORE_DIRS
                for part in file.parts
            ):
                continue

            suffix = file.suffix.lower()

            if suffix not in SUPPORTED_EXTENSIONS:

                continue

            language = LANGUAGE_MAP[suffix]

            language_counter[language] += 1

            stat = file.stat()

            raw_source = file.read_text(
                encoding="utf-8",
                errors="replace"
            )

            relative_path = file.relative_to(workspace)

            files.append(

                SourceDocument(

                    path=file,

                    relative_path=relative_path,

                    extension=suffix,

                    language=language,

                    parser=PARSER_MAP[language],

                    interpreter=INTERPRETER_MAP[language],

                    size=stat.st_size,

                    modified=datetime.fromtimestamp(stat.st_mtime),

                    hash=file_hash(file),

                    raw_source=raw_source

                )

            )

        files.sort(key=lambda x: str(x.path))

        return SourceCollection(

            total_files=len(files),

            languages=dict(language_counter),

            files=files

        )