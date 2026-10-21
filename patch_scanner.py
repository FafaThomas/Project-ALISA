from pathlib import Path

path = Path(r"agents/forge/codebase_interpreter_agent/src/codebase_interpreter_agent/scanner.py")
text = path.read_text(encoding="utf-8-sig")

old = '''    try:
        tree = ast.parse(source, filename=path)
    except (SyntaxError, ValueError) as exc:
        report.errors.append(
            f"Python parse failed at {path}: {exc}"
        )
        return
'''
new = '''    try:
        tree = ast.parse(source, filename=path)
    except (SyntaxError, ValueError) as exc:
        report.errors.append(
            f"Python parse failed at {path}: {exc}"
        )
        return False
'''
assert old in text, "Could not find Python parse error handler"
text = text.replace(old, new, 1)

old = '''    visit(tree, [])


def scan_project(
'''
new = '''    visit(tree, [])
    return True


def scan_project(
'''
assert old in text, "Could not find end of Python extraction function"
text = text.replace(old, new, 1)

old = '''                if size > max_file_bytes:
                    counters["skipped"] += 1
                    report.skipped.append(
                        f"{relative}: exceeds {max_file_bytes} byte limit"
                    )
                    continue
'''
new = '''                if size > max_file_bytes:
                    counters["skipped"] += 1
                    report.skipped.append(
                        f"{relative}: exceeds {max_file_bytes} byte limit"
                    )
                    report.files.append(
                        FileRecord(
                            path=relative,
                            language=_language(path),
                            size_bytes=size,
                            sha256="",
                            status="skipped_too_large",
                        )
                    )
                    log(f"[SKIP FILE] {relative}: {size} bytes exceeds limit")
                    continue
'''
assert old in text, "Could not find large-file handler"
text = text.replace(old, new, 1)

old = '''            report.files.append(
                FileRecord(
                    path=relative,
                    language=language,
                    size_bytes=size,
                    sha256=digest,
                )
            )

            if language == "python":
'''
new = '''            record = FileRecord(
                path=relative,
                language=language,
                size_bytes=size,
                sha256=digest,
            )
            report.files.append(record)

            if language == "python":
'''
assert old in text, "Could not find file record creation"
text = text.replace(old, new, 1)

old = '''                _extract_python(relative, source, report)
                counters["parsed"] += 1
'''
new = '''                parsed_ok = _extract_python(relative, source, report)
                record.status = "analyzed" if parsed_ok else "parse_error"
                counters["parsed"] += 1
'''
assert old in text, "Could not find Python parse call"
text = text.replace(old, new, 1)

path.write_text(text, encoding="utf-8")
print("Applied scanner status and large-file fixes.")
