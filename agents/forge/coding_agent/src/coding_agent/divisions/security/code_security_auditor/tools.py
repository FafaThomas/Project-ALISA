"""Deterministic checks for the Code Security Auditor.

Pattern matches are advisory findings. They complement the model's review
and still run when the model is unavailable.
"""

from __future__ import annotations

import re

_CHECKS: tuple[tuple[str, re.Pattern[str], str, str, str], ...] = (
    # (applies-to suffixes, pattern, severity, issue, suggestion)
    (".py", re.compile(r"\b(eval|exec)\s*\("), "high",
     "eval/exec on dynamic input can run arbitrary code", "parse the input explicitly instead"),
    (".py", re.compile(r"shell\s*=\s*True"), "high",
     "subprocess with shell=True is open to shell injection", "pass an argument list without shell=True"),
    (".py", re.compile(r"\bpickle\.loads?\("), "medium",
     "unpickling untrusted data can run arbitrary code", "use JSON or another data-only format"),
    (".py", re.compile(r"yaml\.load\((?![^)]*Loader\s*=\s*yaml\.SafeLoader)"), "medium",
     "yaml.load without SafeLoader can construct arbitrary objects", "use yaml.safe_load"),
    (".py", re.compile(r"verify\s*=\s*False"), "medium",
     "TLS certificate verification is disabled", "keep verification on"),
    (".py", re.compile(r"hashlib\.(md5|sha1)\("), "low",
     "MD5/SHA-1 are not safe for security purposes", "use sha256 or a password hash such as bcrypt/argon2"),
    ("", re.compile(r"(?i)(password|secret|api_key|token)\s*[:=]\s*['\"][^'\"\s]{6,}['\"]"), "high",
     "a credential appears to be hard-coded", "read it from the environment or a secrets store"),
    ((".py", ".js", ".ts"), re.compile(r"(?i)execute\(\s*f?['\"].*(%s|\{|\+)"), "high",
     "SQL built by string formatting is open to injection", "use parameterized queries"),
    ((".js", ".jsx", ".ts", ".tsx"), re.compile(r"dangerouslySetInnerHTML|\.innerHTML\s*="), "medium",
     "raw HTML injection can lead to XSS", "render text, or sanitize the HTML first"),
)


def review(files: list[tuple[str, str]]) -> list[dict]:
    findings: list[dict] = []
    for path, content in files:
        lower = path.lower()
        for suffixes, pattern, severity, issue, suggestion in _CHECKS:
            if suffixes and not lower.endswith(suffixes):
                continue
            for number, line in enumerate(content.splitlines(), start=1):
                if pattern.search(line):
                    findings.append({
                        "severity": severity, "file": path,
                        "issue": f"line {number}: {issue}", "suggestion": suggestion,
                    })
                    break  # one finding per check per file is enough to act on
    return findings
