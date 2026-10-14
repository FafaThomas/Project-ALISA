from pathlib import Path


EXTENSION_HINTS = {
    ".py": "source_code",
    ".pyi": "source_code",
    ".cs": "source_code",
    ".java": "source_code",
    ".js": "source_code",
    ".jsx": "source_code",
    ".ts": "source_code",
    ".tsx": "source_code",
    ".sql": "database_query_schema_or_migration",
    ".csv": "tabular_dataset",
    ".parquet": "columnar_dataset",
    ".json": "structured_data_or_configuration",
    ".yaml": "structured_data_or_configuration",
    ".yml": "structured_data_or_configuration",
    ".toml": "structured_data_or_configuration",
    ".xml": "structured_data",
    ".pdf": "document",
    ".docx": "document",
    ".doc": "document",
    ".epub": "ebook",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".svg": "vector_graphic",
    ".dcm": "medical_imaging_file",
    ".stl": "3d_geometry",
    ".step": "cad_model",
    ".stp": "cad_model",
    ".dll": "compiled_library",
    ".exe": "compiled_executable",
    ".safetensors": "model_weights",
    ".gguf": "llm_model_artifact",
    ".db": "database_file",
    ".sqlite": "database_file",
    ".sqlite3": "database_file",
    ".bin": "binary_data_unknown",
    ".dat": "data_file_unknown",
    ".zip": "archive",
    ".7z": "archive",
    ".tar": "archive",
    ".gz": "compressed_file",
}

NAME_HINTS = {
    "dockerfile": "container_build_definition",
    "makefile": "build_definition",
    "readme": "project_documentation",
    "license": "license_document",
    "requirements.txt": "python_dependencies",
    "pyproject.toml": "python_project_configuration",
}


def infer_artifact_category(path, is_directory=False):
    """Return a cautious category inferred from a path, not file contents."""
    path = Path(path)
    name = path.name.lower()

    if is_directory:
        return "directory_subtree"

    if name in NAME_HINTS:
        return NAME_HINTS[name]

    return EXTENSION_HINTS.get(
        path.suffix.lower(),
        "unknown_artifact",
    )


def make_artifact_stub(
    relative_path,
    *,
    status,
    reason,
    size_bytes=None,
    modified_time=None,
    is_directory=False,
):
    """Generate searchable metadata without claiming content was inspected."""
    path = Path(relative_path)
    extension = "" if is_directory else path.suffix.lower()
    category = infer_artifact_category(path, is_directory=is_directory)

    inference_basis = ["filename", "relative_path"]
    if extension:
        inference_basis.append("extension")

    lines = [
        "ALISA ARTIFACT METADATA RECORD",
        f"Filename: {path.name or relative_path}",
        f"Relative path: {relative_path}",
        f"Extension: {extension or '[none]'}",
        f"Inferred category: {category}",
        f"Artifact status: {status}",
        f"Status reason: {reason}",
        "Inference basis: " + ", ".join(inference_basis),
        "Content inspected: no",
        "Important: category is inferred from path/name/extension only.",
    ]

    if size_bytes is not None:
        lines.append(f"Size in bytes: {size_bytes}")

    if modified_time is not None:
        lines.append(f"Last modified: {modified_time}")

    return {
        "text": "\n".join(lines),
        "metadata": {
            "record_type": "artifact_stub",
            "extension": extension,
            "inferred_category": category,
            "inference_basis": inference_basis,
            "content_inspected": False,
            "artifact_status": status,
            "status_reason": reason,
            "is_directory": is_directory,
            "size_bytes": size_bytes,
            "modified_time": modified_time,
        },
    }