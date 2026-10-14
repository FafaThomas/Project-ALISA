import csv, json, mimetypes
from pathlib import Path

TEXT_EXTENSIONS = {
    ".py", ".pyi", ".cs", ".java", ".js", ".jsx", ".ts", ".tsx", ".sql",
    ".m", ".md", ".rst", ".txt", ".yaml", ".yml", ".toml", ".ini", ".cfg",
    ".xml", ".html", ".htm", ".css", ".scss", ".sh", ".ps1", ".bat",
    ".c", ".h", ".cpp", ".hpp", ".go", ".rs", ".rb", ".php", ".kt", ".r",
}

def extract(path: Path):
    suffix = path.suffix.lower()
    if path.stat().st_size > 100 * 1024 * 1024:
        raise ValueError("File exceeds 100 MiB safety limit")
    if suffix == ".csv":
        with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as f:
            reader = csv.DictReader(f)
            columns = reader.fieldnames or []
            samples = []
            row_count = 0
            for row in reader:
                row_count += 1
                if len(samples) < 40:
                    samples.append(row)
        profile = {"file": path.name, "row_count": row_count, "columns": columns,
                   "sample_rows": samples,
                   "note": "Bounded profile/sample only; raw rows are not individually embedded."}
        return json.dumps(profile, ensure_ascii=False, indent=2), "csv_profile", "text/csv", {"row_count": row_count, "columns": columns}
    if suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), "json", "application/json", {"top_level_type": type(data).__name__}
    if suffix in TEXT_EXTENSIONS or path.name.lower() in {"dockerfile", "makefile"}:
        value = path.read_text(encoding="utf-8-sig", errors="replace")
        return value, "generic_text", mimetypes.guess_type(path.name)[0] or "text/plain", {"line_count": value.count("\n") + (1 if value else 0)}
    raise NotImplementedError(f"No extractor registered for {suffix or 'extensionless file'}")
