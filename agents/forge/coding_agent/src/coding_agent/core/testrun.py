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

Dependencies: when the project has ``requirements.txt`` files, their packages
are installed into a throwaway virtual environment, cached by the requirement
list, and the commands run with it. Only plain ``name[extras] <version spec>``
lines are installed, as prebuilt wheels (``--only-binary=:all:``, so no package
build scripts run at install time); URLs, local paths and pip options are
refused. A command that fails only because a service is not running (a
database server, for example) is reported as "needs a service", not as a crash.

``CODING_AGENT_TEST_RUN``: ``repair`` (default) runs, repairs and reports what
is left as warnings; ``block`` also refuses to write while failures remain;
``off`` skips the test run. ``CODING_AGENT_TEST_TIMEOUT``: seconds per command
(default 20). ``CODING_AGENT_TEST_DEPS``: ``install`` (default) or ``off``.
``CODING_AGENT_TEST_ENV_DIR``: where environments are cached (default
``~/.alisa/forge/testrun-envs``). ``CODING_AGENT_TEST_INSTALL_TIMEOUT``: seconds
for one install (default 600).
"""

from __future__ import annotations

import hashlib
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
DEFAULT_INSTALL_TIMEOUT = 600.0
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
    unavailable: str = ""      # it ran, but failed only for lack of a service or package
    ran_as: str = ""           # the command as run, when its script was found in a subfolder
    reported: str = ""         # an error report without a traceback that still means a bug (Django checks)
    stdout: str = ""
    stderr: str = ""

    @property
    def crashed(self) -> bool:
        return "Traceback (most recent call last)" in self.stderr

    @property
    def failed(self) -> bool:
        return not self.skipped and not self.unavailable and (self.timed_out or self.crashed or bool(self.reported))

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


def deps_mode() -> str:
    load_local_env()
    return "off" if (env_value("CODING_AGENT_TEST_DEPS") or "install").lower() == "off" else "install"


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
        # No database server runs here: make PostgreSQL clients give up quickly instead of hanging.
        "PGCONNECT_TIMEOUT": "3",
    }
    for name in ("SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "LANG", "LC_ALL", "TZ"):
        if os.environ.get(name):
            env[name] = os.environ[name]
    return env


def _find_script(project: Path, script: str) -> str | None:
    """A planned ``python manage.py`` when manage.py lives in a subfolder: the one file with that name."""
    name = PurePosixPath(script).name
    matches = [p.relative_to(project).as_posix() for p in project.rglob(name) if p.is_file()]
    return matches[0] if len(matches) == 1 else None


def to_argv(command: str, project: Path, python: str | None = None) -> tuple[list[str] | None, str, str]:
    """Validate a planned command.

    Returns (argv, folder to run it in relative to the project, "") or (None, "", reason it was refused).
    """
    python = python or sys.executable
    if re.search(r"<[A-Za-z_][^<>]*>", command):
        return None, "", "contains a placeholder instead of a concrete value"
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError as exc:
        return None, "", f"could not parse: {exc}"
    if len(tokens) < 2 or PurePosixPath(tokens[0].replace("\\", "/")).name.lower() not in _PYTHON_NAMES:
        return None, "", "only 'python <project file>' or 'python -m <project module>' commands are run"
    if tokens[1] == "-m":
        if len(tokens) < 3:
            return None, "", "missing module name"
        module = tokens[2].replace(".", "/")
        if not ((project / f"{module}.py").is_file() or (project / module / "__main__.py").is_file()):
            return None, "", f"module {tokens[2]!r} is not part of the project"
        return [python, "-E", "-s", *tokens[1:]], ".", ""
    script = tokens[1].replace("\\", "/")
    pure = PurePosixPath(script)
    if pure.is_absolute() or ".." in pure.parts or pure.suffix != ".py":
        return None, "", f"{tokens[1]!r} is not a Python file in the project"
    folder = "."
    if not (project / script).is_file():
        found = _find_script(project, script) if len(pure.parts) == 1 else None
        if found is None:
            return None, "", f"{tokens[1]!r} is not a Python file in the project"
        # Run it where it lives, as a user would (cd backend; python manage.py ...).
        folder = str(PurePosixPath(found).parent)
    target = (project / folder / script).resolve() if folder != "." else (project / script).resolve()
    if project.resolve() not in target.parents:
        return None, "", f"{tokens[1]!r} is not a Python file in the project"
    return [python, "-E", "-s", script, *tokens[2:]], folder, ""


# ------------------------------------------------------------------ dependencies
_REQUIREMENT = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*"                       # name
    r"(\[[A-Za-z0-9._, -]+\])?"                          # [extras]
    r"(\s*(===|==|>=|<=|~=|!=|>|<)\s*[A-Za-z0-9.*+!_-]+\s*,?)*"   # version specifiers
    r"(\s*;\s*[A-Za-z0-9_.'\" <>=!~()]+)?$"              # environment marker
)


def parse_requirements(text: str) -> tuple[list[str], list[str]]:
    """(lines that will be installed, lines refused: options, URLs, local paths, includes)."""
    accepted, refused = [], []
    for raw in text.splitlines():
        line = raw.split(" #")[0].strip()
        if not line or line.startswith("#"):
            continue
        (accepted if _REQUIREMENT.match(line) else refused).append(line)
    return accepted, refused


def requirement_files(changes: ChangeSet) -> list[str]:
    files = set(changes.workspace.list_files()) | {c.path for c in changes.changes() if c.action != "delete"}
    return sorted(p for p in files if PurePosixPath(p).name.lower() == "requirements.txt")


@dataclass
class PythonEnv:
    python: str
    note: str = ""           # what was prepared, for the run's output
    warning: str = ""        # why dependencies are not available
    packages: tuple[str, ...] = ()

    @property
    def has_dependencies(self) -> bool:
        return bool(self.packages) and not self.warning


_PREPARED: dict[str, PythonEnv] = {}


def env_dir() -> Path:
    load_local_env()
    configured = env_value("CODING_AGENT_TEST_ENV_DIR")
    return Path(configured).expanduser() if configured else Path.home() / ".alisa" / "forge" / "testrun-envs"


def install_timeout() -> float:
    load_local_env()
    try:
        return max(30.0, float(env_value("CODING_AGENT_TEST_INSTALL_TIMEOUT") or DEFAULT_INSTALL_TIMEOUT))
    except ValueError:
        return DEFAULT_INSTALL_TIMEOUT


def _venv_python(folder: Path) -> Path:
    return folder / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _install_env(cache: Path) -> dict[str, str]:
    """What pip needs to reach the package index; no credentials from the agent's environment."""
    env = {"PATH": os.environ.get("PATH", ""), "PIP_DISABLE_PIP_VERSION_CHECK": "1", "PIP_NO_INPUT": "1",
           "PIP_CACHE_DIR": str(cache), "PYTHONIOENCODING": "utf-8"}
    for name in ("SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "TEMP", "TMP", "TMPDIR", "HOME", "USERPROFILE",
                 "LOCALAPPDATA", "APPDATA", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy",
                 "no_proxy", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "PIP_INDEX_URL", "PIP_EXTRA_INDEX_URL",
                 "PIP_TRUSTED_HOST", "PIP_CERT"):
        if os.environ.get(name):
            env[name] = os.environ[name]
    return env


def prepare_python(changes: ChangeSet) -> PythonEnv:
    """The interpreter test commands run with: a cached environment with the project's requirements, if any."""
    files = requirement_files(changes)
    if not files or deps_mode() == "off":
        return PythonEnv(sys.executable)
    accepted: list[str] = []
    refused: list[str] = []
    for path in files:
        ok, bad = parse_requirements(changes.current(path) or "")
        accepted += ok
        refused += [f"{path}: {line}" for line in bad]
    packages = tuple(sorted(set(accepted), key=str.lower))
    if not packages:
        return PythonEnv(sys.executable, warning="no installable lines in " + ", ".join(files) if refused else "")
    refused_note = f"; refused {len(refused)} line(s): {'; '.join(refused)[:200]}" if refused else ""
    key = hashlib.sha256("\n".join((sys.version, *packages)).encode()).hexdigest()[:16]
    if key in _PREPARED:
        return _PREPARED[key]
    root = env_dir()
    folder = root / key
    python = _venv_python(folder)
    if (folder / ".ready").is_file() and python.is_file():
        result = PythonEnv(str(python), f"{len(packages)} package(s) from {', '.join(files)} (cached)"
                           + refused_note, packages=packages)
        _PREPARED[key] = result
        return result
    shutil.rmtree(folder, ignore_errors=True)
    try:
        folder.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([sys.executable, "-m", "venv", str(folder)], check=True, capture_output=True, timeout=300,
                       stdin=subprocess.DEVNULL)
        done = subprocess.run(
            [str(python), "-m", "pip", "install", "--only-binary=:all:", *packages],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=install_timeout(),
            env=_install_env(root / "pip-cache"), stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as exc:
        shutil.rmtree(folder, ignore_errors=True)
        result = PythonEnv(sys.executable, warning=f"could not create the test environment: {exc}",
                           packages=packages)
        _PREPARED[key] = result
        return result
    if done.returncode != 0:
        shutil.rmtree(folder, ignore_errors=True)
        tail = " ".join((done.stderr or done.stdout).strip().splitlines()[-3:])[:400]
        result = PythonEnv(sys.executable, warning=f"installing {', '.join(files)} failed: {tail}",
                           packages=packages)
        _PREPARED[key] = result
        return result
    (folder / ".ready").write_text("\n".join(packages), encoding="utf-8")
    result = PythonEnv(str(python), f"installed {len(packages)} package(s) from {', '.join(files)}" + refused_note,
                       packages=packages)
    _PREPARED[key] = result
    return result


# ------------------------------------------------------------------ running
_SERVICE_ERRORS = re.compile(
    r"(OperationalError|ConnectionRefusedError|ConnectionError|InterfaceError).*"
    r"(connect|refused|server|host|socket|timeout|Connection)", re.I)
# Connection failures whose message spans several lines (psycopg, redis, requests).
_SERVICE_TEXT = re.compile(
    r"connection (timeout expired|refused|failed)|could not connect|Is the server running|"
    r"Connection refused|Name or service not known|getaddrinfo failed|Max retries exceeded", re.I)
_MISSING_MODULE = re.compile(r"No module named '([\w.]+)'")
_DOTTED = re.compile(r"'([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+)'")


TRACEBACK = "Traceback (most recent call last):"


def reported_errors(command: str, result: CommandResult) -> str:
    """Framework error reports that exit without a traceback but still mean the code is wrong."""
    if result.exit_code in (0, None):
        return ""
    if "System check identified" in result.stderr and "ERRORS:" in result.stderr:
        return "Django system check reported errors"
    if "makemigrations" in command and "--check" in command and "Migrations for" in result.stdout:
        return "models changed but the migrations for them are missing"
    missing = _MISSING_MODULE.search(result.stderr)
    if missing:  # an import error caught and printed without a traceback (Django's manage.py does this)
        return f"could not import module '{missing.group(1)}'"
    return ""


def _last_error(stderr: str) -> str:
    lines = [line for line in stderr.strip().splitlines() if line.strip()]
    return lines[-1] if lines else ""


def classify(result: CommandResult, env: PythonEnv, project: Path, folder: str) -> None:
    """Mark a crash that only shows a missing service or package as unavailable, not as a bug."""
    if not (result.crashed or result.reported):
        return
    last = _last_error(result.stderr)
    if _SERVICE_ERRORS.search(last) or _SERVICE_TEXT.search(result.stderr):
        result.unavailable = "needs a service that is not running in the test run (database, network)"
        return
    missing = _MISSING_MODULE.search(result.stderr)
    if missing:
        top = missing.group(1).split(".")[0]
        base = project / folder
        in_project = any((root / f"{top}.py").is_file() or (root / top).is_dir() for root in (project, base))
        if not in_project and not env.has_dependencies:
            reason = env.warning or "the project has no requirements.txt"
            result.unavailable = f"package '{top}' is not installed in the test run ({reason})"


def run_commands(changes: ChangeSet, commands: list[dict[str, str]], seconds: float,
                 env: PythonEnv | None = None) -> list[CommandResult]:
    env = env or PythonEnv(sys.executable)
    project = build_copy(changes)
    results: list[CommandResult] = []
    try:
        variables = sandbox_env(project)
        for spec in commands[:MAX_COMMANDS]:
            command, purpose = str(spec.get("command", "")).strip(), str(spec.get("purpose", "")).strip()
            result = CommandResult(command=command, purpose=purpose)
            argv, folder, refusal = to_argv(command, project, env.python)
            if argv is None:
                result.skipped = refusal
                results.append(result)
                continue
            if folder != ".":
                result.ran_as = f"cd {folder} && {command}"
            try:
                done = subprocess.run(argv, cwd=project / folder, env=variables, stdin=subprocess.DEVNULL,
                                      capture_output=True, text=True, encoding="utf-8", errors="replace",
                                      timeout=seconds)
                result.exit_code = done.returncode
                result.stdout, result.stderr = done.stdout, done.stderr
            except subprocess.TimeoutExpired as exc:
                result.timed_out = True
                result.stdout = (exc.stdout or b"").decode("utf-8", "replace") if isinstance(exc.stdout, bytes) \
                    else (exc.stdout or "")
                result.stderr = (exc.stderr or b"").decode("utf-8", "replace") if isinstance(exc.stderr, bytes) \
                    else (exc.stderr or "")
            result.reported = reported_errors(command, result)
            for attr in ("stdout", "stderr"):
                text = getattr(result, attr).replace(str(project) + os.sep, "").replace(str(project), ".")
                tail = text[-OUTPUT_TAIL:]
                if attr == "stderr" and TRACEBACK in text and TRACEBACK not in tail:
                    tail = f"{TRACEBACK}\n  ... [earlier frames cut]\n{tail}"
                setattr(result, attr, tail)
            classify(result, env, project, folder)
            results.append(result)
    finally:
        shutil.rmtree(project.parent, ignore_errors=True)
    return results


def _in_project(top: str, files: set[str]) -> bool:
    return any(f == f"{top}.py" or f.endswith(f"/{top}.py") or f.startswith(f"{top}/") or f"/{top}/" in f
               for f in files)


def problems_from(results: list[CommandResult], changes: ChangeSet, seconds: float) -> list[dict[str, str]]:
    """Failures as files-gate style problems, pointed at the project file where they happened."""
    project_files = set(changes.paths()) | set(changes.workspace.list_files())
    requirements = requirement_files(changes)
    problems = []
    for result in results:
        if not result.failed:
            continue
        path = ""
        missing = _MISSING_MODULE.search(result.stderr)
        if missing and _in_project(missing.group(1).split(".")[0], project_files):
            missing = None  # a project module imported the wrong way: the traceback points at the bug
        if missing and requirements:
            # Installed requirements but the package is still missing: requirements.txt is incomplete.
            path = requirements[0] if len(requirements) == 1 else ""
        for frame_path, _ in reversed(_FRAME.findall(result.stderr)):
            if path:
                break
            rel = frame_path.replace("\\", "/").lstrip("./")
            if rel in project_files:
                path = rel
            else:
                path = next((f for f in project_files if rel and f.endswith("/" + rel)), "")
        if not path:
            # Django names the culprit by module: "<class 'menu.admin.MenuItemAdmin'>" -> menu/admin.py
            for dotted in _DOTTED.findall(result.stderr):
                parts = dotted.split(".")
                for end in range(len(parts), 1, -1):
                    suffix = "/".join(parts[:end]) + ".py"
                    match = next((f for f in sorted(project_files) if f == suffix or f.endswith("/" + suffix)), "")
                    if match:
                        path = match
                        break
                if path:
                    break
        if not path:
            tokens = result.command.split()
            script = tokens[1] if len(tokens) > 1 and tokens[1] != "-m" else ""
            folder = result.ran_as[3:].split(" && ")[0] if result.ran_as.startswith("cd ") else ""
            if script and folder:
                script = f"{folder}/{script}"
            path = script or (changes.paths() or ["?"])[0]
        what = (f"hung: no exit within {seconds:.0f}s" if result.timed_out else
                "crashed with a traceback" if result.crashed else f"failed: {result.reported}")
        if missing and requirements and path in requirements:
            what += f" (package '{missing.group(1).split('.')[0]}' is imported but not listed in {path})"
        output = result.stderr if result.stderr.strip() else result.stdout
        tail = "\n".join(output.strip().splitlines()[-12:])
        ran = f" (run as `{result.ran_as}`)" if result.ran_as else ""
        problems.append({
            "path": path, "check": "test-run",
            "detail": f"`{result.command}`{ran} {what}" + (f"\n{tail}" if tail else ""),
        })
    return problems
