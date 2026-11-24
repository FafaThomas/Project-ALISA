"""The Debugging Specialist's test run: execute the generated program briefly, in a throwaway copy.

After the files gate passes, the Debugging Specialist plans a few commands that
exercise what the request asked for. They run against a temporary copy of the
project plus the proposed changes, never the workspace itself. A command that
hangs (no exit within the timeout) or ends in a Python traceback becomes a
repair problem, like a compile error. A program that exits with its own error
message is not a failure: that can be correct behaviour.

Safety:

- only ``python <project .py file> ...`` or ``python -m <project module> ...``,
  run without a shell (no pip, no shell tools, no other programs);
- a per-command timeout, closed stdin (input prompts end instead of hanging);
- a minimal environment: no API keys or tokens; HOME, USERPROFILE, APPDATA and
  TEMP point inside the throwaway copy;
- the copy holds only project files (secrets and .coding-agent are never
  copied) and is deleted afterwards.

``CODING_AGENT_TEST_RUN``: ``repair`` (default) runs, repairs and reports what
is left as warnings; ``block`` also refuses to write while failures remain;
``off`` skips the test run. ``CODING_AGENT_TEST_TIMEOUT``: seconds per command
(default 20).
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath

from .env import env_value, load_local_env
from .workspace import ChangeSet

MAX_COMMANDS = 8
DEFAULT_TIMEOUT = 20.0
OUTPUT_TAIL = 1500
_PYTHON_NAMES = {"python", "python3", "py", "python.exe"}
_FRAME = re.compile(r'File "([^"]+)", line (\d+)')


@dataclass
class CommandResult:
    command: str
    purpose: str = ""
    exit_code: int | None = None
    timed_out: bool = False
    skipped: str = ""          # why the command was not run
    stdout: str = ""
    stderr: str = ""

    @property
    def crashed(self) -> bool:
        return "Traceback (most recent call last)" in self.stderr

    @property
    def failed(self) -> bool:
        return not self.skipped and (self.timed_out or self.crashed)

    def to_dict(self) -> dict:
        return asdict(self)


def mode() -> str:
    load_local_env()
    value = (env_value("CODING_AGENT_TEST_RUN") or "repair").lower()
    return value if value in ("off", "repair", "block") else "repair"


def timeout() -> float:
    load_local_env()
    try:
        return max(1.0, float(env_value("CODING_AGENT_TEST_TIMEOUT") or DEFAULT_TIMEOUT))
    except ValueError:
        return DEFAULT_TIMEOUT


def has_python(changes: ChangeSet) -> bool:
    return any(c.path.endswith(".py") and c.action != "delete" for c in changes.changes())


def fallback_commands(changes: ChangeSet) -> list[dict[str, str]]:
    """When no plan is available: ``--help`` on every changed script with a main guard."""
    commands = []
    for change in changes.changes():
        content = changes.current(change.path) or ""
        if change.path.endswith(".py") and "__main__" in content:
            commands.append({"command": f"python {change.path} --help", "purpose": "starts without crashing"})
    return commands[:MAX_COMMANDS]


# ------------------------------------------------------------------ sandbox
def build_copy(changes: ChangeSet) -> Path:
    """A throwaway copy of the project with the proposed changes applied."""
    root = Path(tempfile.mkdtemp(prefix="coding-agent-testrun-"))
    project = root / "project"
    project.mkdir()
    workspace = changes.workspace
    for rel in workspace.list_files():
        source = workspace.root / rel
        target = project / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(source, target)
        except OSError:
            continue
    for change in changes.changes():
        target = project / change.path
        if change.action == "delete":
            target.unlink(missing_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(change.content, encoding="utf-8", newline="\n")
    for name in ("home", "tmp"):
        (root / name).mkdir()
    return project


def sandbox_env(project: Path) -> dict[str, str]:
    """Only what Python needs; no credentials from the agent's environment."""
    home, tmp = str(project.parent / "home"), str(project.parent / "tmp")
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "HOME": home, "USERPROFILE": home, "APPDATA": home, "LOCALAPPDATA": home,
        "TEMP": tmp, "TMP": tmp, "TMPDIR": tmp,
    }
    for name in ("SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "LANG", "LC_ALL", "TZ"):
        if os.environ.get(name):
            env[name] = os.environ[name]
    return env


def to_argv(command: str, project: Path) -> tuple[list[str] | None, str]:
    """Validate a planned command. Returns (argv, "") or (None, reason it was refused)."""
    if re.search(r"<[A-Za-z_][^<>]*>", command):
        return None, "contains a placeholder instead of a concrete value"
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError as exc:
        return None, f"could not parse: {exc}"
    if len(tokens) < 2 or PurePosixPath(tokens[0].replace("\\", "/")).name.lower() not in _PYTHON_NAMES:
        return None, "only 'python <project file>' or 'python -m <project module>' commands are run"
    if tokens[1] == "-m":
        if len(tokens) < 3:
            return None, "missing module name"
        module = tokens[2].replace(".", "/")
        if not ((project / f"{module}.py").is_file() or (project / module / "__main__.py").is_file()):
            return None, f"module {tokens[2]!r} is not part of the project"
        return [sys.executable, "-E", "-s", *tokens[1:]], ""
    script = tokens[1].replace("\\", "/")
    target = (project / script).resolve()
    if PurePosixPath(script).is_absolute() or ".." in PurePosixPath(script).parts or \
            not target.is_file() or project.resolve() not in target.parents or target.suffix != ".py":
        return None, f"{tokens[1]!r} is not a Python file in the project"
    return [sys.executable, "-E", "-s", script, *tokens[2:]], ""


def run_commands(changes: ChangeSet, commands: list[dict[str, str]], seconds: float) -> list[CommandResult]:
    project = build_copy(changes)
    results: list[CommandResult] = []
    try:
        env = sandbox_env(project)
        for spec in commands[:MAX_COMMANDS]:
            command, purpose = str(spec.get("command", "")).strip(), str(spec.get("purpose", "")).strip()
            result = CommandResult(command=command, purpose=purpose)
            argv, refusal = to_argv(command, project)
            if argv is None:
                result.skipped = refusal
                results.append(result)
                continue
            try:
                done = subprocess.run(argv, cwd=project, env=env, stdin=subprocess.DEVNULL, capture_output=True,
                                      text=True, encoding="utf-8", errors="replace", timeout=seconds)
                result.exit_code = done.returncode
                result.stdout, result.stderr = done.stdout, done.stderr
            except subprocess.TimeoutExpired as exc:
                result.timed_out = True
                result.stdout = (exc.stdout or b"").decode("utf-8", "replace") if isinstance(exc.stdout, bytes) \
                    else (exc.stdout or "")
                result.stderr = (exc.stderr or b"").decode("utf-8", "replace") if isinstance(exc.stderr, bytes) \
                    else (exc.stderr or "")
            for attr in ("stdout", "stderr"):
                text = getattr(result, attr).replace(str(project) + os.sep, "").replace(str(project), ".")
                setattr(result, attr, text[-OUTPUT_TAIL:])
            results.append(result)
    finally:
        shutil.rmtree(project.parent, ignore_errors=True)
    return results


def problems_from(results: list[CommandResult], changes: ChangeSet, seconds: float) -> list[dict[str, str]]:
    """Failures as files-gate style problems, pointed at the project file where they happened."""
    project_files = set(changes.paths()) | set(changes.workspace.list_files())
    problems = []
    for result in results:
        if not result.failed:
            continue
        path = ""
        for frame_path, _ in reversed(_FRAME.findall(result.stderr)):
            rel = frame_path.replace("\\", "/").lstrip("./")
            if rel in project_files:
                path = rel
                break
        if not path:
            tokens = result.command.split()
            path = tokens[1] if len(tokens) > 1 and tokens[1] != "-m" else (changes.paths() or ["?"])[0]
        what = f"hung: no exit within {seconds:.0f}s" if result.timed_out else "crashed with a traceback"
        tail = "\n".join(result.stderr.strip().splitlines()[-12:])
        problems.append({
            "path": path, "check": "test-run",
            "detail": f"`{result.command}` {what}" + (f"\n{tail}" if tail else ""),
        })
    return problems
