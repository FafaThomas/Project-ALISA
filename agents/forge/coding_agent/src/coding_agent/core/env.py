"""Read the ALISA project ``.env`` into the environment (stdlib only)."""

from __future__ import annotations

import os
from pathlib import Path


def find_project_env() -> Path | None:
    """The ALISA project .env, found by walking up from this file."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "README.MD").is_file() and (parent / "agents").is_dir():
            env = parent / ".env"
            return env if env.is_file() else None
    return None


def load_local_env() -> None:
    """Load project .env values without overriding variables already set.

    Lines are ``KEY=value``; blank lines and ``#`` comment lines are skipped.
    Put comments on their own lines (a ``#`` after a value is part of the value).
    """
    env_path = find_project_env()
    if env_path is None:
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key:
            os.environ.setdefault(key, value.strip().strip("'\""))


def env_value(name: str) -> str | None:
    """A non-empty environment value (blank values in .env count as unset)."""
    value = os.getenv(name, "").strip()
    return value or None
