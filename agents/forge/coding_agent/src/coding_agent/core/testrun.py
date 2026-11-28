"""The Debugging Specialist's test run: execute the generated program briefly, in a throwaway copy.

After the files gate passes, the Debugging Specialist plans a few commands that
exercise what the request asked for. They run against a temporary copy of the
project plus the proposed changes, never the workspace itself. A command that
hangs (no exit within the timeout) or ends in a Python traceback becomes a
repair problem, like a compile error. A program that exits with its own error
message is not a failure: that can be correct behaviour.

Some programs run until they are stopped, and that is not a hang: a server
that has started, and a game or a desktop or terminal UI (pygame, tkinter,
curses ...), which waits for its user. Such a program still running at the
timeout, without a traceback, started fine. SDL programs (pygame) run with no
window and no sound, so nothing appears on the user's screen.

The other way round, a game or UI with an event loop that ends by itself
(started with no arguments, no input given) is a bug: it would close on its
user too. That is how quitting at game over, after a number of steps, or on
detecting a test environment shows up (``closed_by_itself``).

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
import time
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
# Django subcommands that serve until stopped, or wait for input on a terminal.
_SERVER_SUBCOMMANDS = {"runserver", "runserver_plus", "runworker", "testserver", "shell", "dbshell", "shell_plus"}
# What a program prints when it has started serving: a timeout after this is expected, not a hang.
_SERVING_TEXT = re.compile(
    r"Starting development server|Quit the server with|Running on https?://|Serving HTTP on|Listening (?:at|on)|"
    r"Uvicorn running on|Booting worker|Application startup complete", re.I)
# Libraries whose programs run until their user quits: games, desktop and terminal UIs.
_INTERACTIVE_LIBS = (r"pygame|pygame_gui|pyglet|arcade|ursina|panda3d|pyray|raylib|tkinter|customtkinter|turtle|"
                     r"curses|PyQt5|PyQt6|PySide2|PySide6|kivy|wx|textual|urwid|flet|dearpygui")
_INTERACTIVE_IMPORT = re.compile(
    rf"(?m)^\s*(?:from\s+({_INTERACTIVE_LIBS})\b|import\s+(?:[\w.]+(?:\s+as\s+\w+)?\s*,\s*)*({_INTERACTIVE_LIBS})\b)")
_IMPORT = re.compile(r"(?m)^\s*(?:from\s+(\.*[\w.]*)\s+import\b|import\s+([\w.]+(?:\s+as\s+\w+)?"
                     r"(?:\s*,\s*[\w.]+(?:\s+as\s+\w+)?)*))")
# Calls that wait for or read the user's input: an event loop. A program that uses a game or UI library only to
# draw something and exit (rendering an image, say) has none of these, and may end by itself.
_READS_INPUT = re.compile(
    r"\bevent\.(?:get|wait|poll)\(|\bget_pressed\(|mainloop\(|\b(?:getch|getkey|get_wch)\(|\bexec_?\(\)|"
    r"\b(?:app|arcade|pyglet\.app|base)\.run\(|\w*App\([^)]*\)\.run\(|\bexitonclick\(|\bturtle\.done\(|"
    r"\bstart_dearpygui\(|\b(?:window_should_close|WindowShouldClose)\(|\b(?:ft|flet)\.app\(")


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
        # Games on SDL (pygame) run without a window or sound: nothing appears on the user's screen.
        "SDL_VIDEODRIVER": "dummy", "SDL_AUDIODRIVER": "dummy", "PYGAME_HIDE_SUPPORT_PROMPT": "1",
    }
    for name in ("SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "LANG", "LC_ALL", "TZ"):
        if os.environ.get(name):
            env[name] = os.environ[name]
    # Settings documented in .env.example (placeholders, never real secrets): code that requires them can start.
    for name, value in example_env(project).items():
        env.setdefault(name, value)
    return env


EXAMPLE_ENV_NAMES = (".env.example", ".env.sample", ".env.template", "env.example")
_ENV_LINE = re.compile(r"^\s*(?:export\s+)?([A-Z_][A-Z0-9_]*)\s*=\s*(.*?)\s*$")


def example_env(project: Path) -> dict[str, str]:
    """KEY=value pairs from the project's .env.example files (root first, then one folder down)."""
    values: dict[str, str] = {}
    candidates = [project / n for n in EXAMPLE_ENV_NAMES]
    try:
        candidates += [child / n for child in sorted(project.iterdir()) if child.is_dir() for n in EXAMPLE_ENV_NAMES]
    except OSError:
        pass
    for path in candidates:
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for line in text.splitlines():
            found = _ENV_LINE.match(line)
            if found and not line.lstrip().startswith("#"):
                value = found.group(2)
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                values.setdefault(found.group(1), value)
    return values


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
    if pure.name == "manage.py" and len(tokens) > 2 and tokens[2] in _SERVER_SUBCOMMANDS:
        return None, "", f"'{tokens[2]}' starts a server that runs until stopped; the test run needs commands that exit"
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
    why = not_a_script(target, pure.name)
    if why:
        return None, "", why
    return [python, "-E", "-s", script, *tokens[2:]], folder, ""


_DJANGO_IMPORT = re.compile(r"(?m)^\s*(?:from|import)\s+(?:django|rest_framework)\b")
_DJANGO_BOOT = re.compile(r"django\.setup\(|execute_from_command_line|settings\.configure\(|get_wsgi_application|"
                          r"get_asgi_application")
_RELATIVE_IMPORT = re.compile(r"(?m)^(?:from\s+\.)")


def not_a_script(path: Path, name: str) -> str:
    """Why running ``path`` directly can only fail before its own code runs, or "" when it is a real entry point.

    In a real run the Debugging Specialist planned ``python backend/orders/serializers.py`` and five more
    like it: every one crashed on "settings are not configured" or "No module named 'orders'", and the
    repair round that followed rewrote six files to fix crashes that were never bugs.
    """
    if name == "manage.py":
        return ""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    if _DJANGO_IMPORT.search(text) and not _DJANGO_BOOT.search(text):
        return (f"{name} is a module of the Django project, not a script: run directly it fails before its code "
                "runs (settings are not configured). Use manage.py (check, or a management command) instead")
    if _RELATIVE_IMPORT.search(text) and "__main__" not in text:
        return f"{name} is a module of a package (it uses relative imports), not a script: it cannot run directly"
    return ""


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
    problem: str = ""        # a requirements bug the user would hit too (a pinned version that does not exist)
    problem_path: str = ""

    @property
    def has_dependencies(self) -> bool:
        return bool(self.packages) and not self.warning


_PREPARED: dict[str, PythonEnv] = {}
_NOT_FOUND = re.compile(
    r"Could not find a version that satisfies the requirement ([^\s;]+)[^\n]*?\(from versions: ([^)]*)\)")


def unavailable_pin(output: str, files: list[str], changes: ChangeSet) -> tuple[str, str]:
    """(requirements file, problem) when pip failed because a pinned version does not exist here.

    Only when other versions of the package exist: "from versions: none" usually means the package
    has no prebuilt wheel, which the test run needs but a user's ``pip install`` might not.
    Network errors and the like are left as warnings.
    """
    found = _NOT_FOUND.search(output)
    if not found:
        return "", ""
    requirement, versions = found.group(1), [v.strip() for v in found.group(2).split(",") if v.strip()]
    if not versions or versions == ["none"]:
        return "", ""
    name = re.split(r"[\[=<>!~ ]", requirement, maxsplit=1)[0].lower().replace("_", "-")
    stem = name.split("-")[0]
    path = next((f for f in files if stem in (changes.current(f) or "").lower()), files[0])
    target = f"Python {sys.version_info.major}.{sys.version_info.minor} on {sys.platform}"
    return path, (f"pip install -r {path} fails: no {requirement} for {target}. Versions that exist: "
                  f"{', '.join(versions[-8:])}. Pin one of those (or a compatible range such as '>=' the "
                  "lowest that works), keeping the package and its extras.")


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
        result.problem_path, result.problem = unavailable_pin(f"{done.stderr}\n{done.stdout}", files, changes)
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
    words = command.split()
    if "manage.py" in command and "Available subcommands:" in result.stdout and len(words) > 2 \
            and "help" not in words[2:] and not any(w in ("-h", "--help") for w in words):
        # Asked to run a subcommand, got Django's command list: the subcommand never ran.
        return (f"Django printed its command list instead of running '{words[2]}': manage.py most likely passes "
                "sys.argv[1:] to execute_from_command_line (it must pass sys.argv)")
    if "manage.py" in command and "Unknown command:" in result.stderr:
        return "Django did not recognise the subcommand: " + result.stderr.strip().splitlines()[0][:160]
    if result.exit_code in (0, None):
        return ""
    if "System check identified" in result.stderr and "ERRORS:" in result.stderr:
        return "Django system check reported errors"
    if "makemigrations" in command and "--check" in command and "Migrations for" in result.stdout:
        return "models changed but the migrations for them are missing"
    missing = missing_package(result)
    if missing:  # an import error caught and printed without a traceback (Django's manage.py does this)
        return f"could not import module '{missing}'"
    return ""


_MISSING_HINT = re.compile(
    r"(?:could not|couldn't|cannot|can't|unable to) (?:find|import|load) (?:the )?(?:module |package )?"
    r"['\"]?([A-Za-z_][\w.]*)", re.I)


def missing_package(result: CommandResult) -> str | None:
    """The module a command could not import, from a traceback or a friendlier printed message."""
    text = f"{result.stderr}\n{result.stdout}"
    found = _MISSING_MODULE.search(text)
    if found:
        return found.group(1)
    hint = _MISSING_HINT.search(text)
    if hint and "install" in text.lower():
        return hint.group(1).rstrip(".").lower()
    return None


def _last_error(stderr: str) -> str:
    lines = [line for line in stderr.strip().splitlines() if line.strip()]
    return lines[-1] if lines else ""


def _program_sources(entry: Path, project: Path, depth: int = 3):
    """The text of ``entry`` and of the project modules it imports, up to ``depth`` imports away."""
    seen: set[Path] = set()
    queue = [(entry, 0)]
    while queue:
        path, level = queue.pop(0)
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        yield text
        if level >= depth:
            continue
        for source, plain in _IMPORT.findall(text):
            names = [source.lstrip(".")] if source else [part.split()[0] for part in plain.split(",")]
            for name in filter(None, names):
                rel = name.replace(".", "/")
                for base in (path.parent, project):
                    queue += [(base / f"{rel}.py", level + 1), (base / rel / "__init__.py", level + 1)]


def interactive_library(entry: Path, project: Path, depth: int = 3) -> str:
    """The game or UI library ``entry`` is built on, directly or through the project modules it imports
    (up to ``depth`` imports away), or "" when it has none."""
    for text in _program_sources(entry, project, depth):
        found = _INTERACTIVE_IMPORT.search(text)
        if found:
            return found.group(1) or found.group(2)
    return ""


# Runs a command again with Python reporting exceptions that project code caught itself (3.12+:
# sys.monitoring). argv after -c: the project root, then the script (or -m module) and its arguments.
CAUGHT_MARK = "[test run] The program caught this exception itself, so only its message (if any) was printed:"
_CAUGHT_BOOTSTRAP = r'''
import os, runpy, sys, traceback
project = os.path.normcase(os.path.abspath(sys.argv.pop(1)))
caught = []
IGNORED = (ImportError, StopIteration, StopAsyncIteration, GeneratorExit, KeyboardInterrupt, SystemExit)

def handled(code, offset, exc):
    name = code.co_filename  # "<frozen importlib._bootstrap>" and the like are Python's own, not files
    if name.startswith("<") or isinstance(exc, IGNORED):
        return
    if os.path.normcase(os.path.abspath(name)).startswith(project):
        caught[:] = [exc]

monitoring = getattr(sys, "monitoring", None)
if monitoring is not None:
    tool = monitoring.DEBUGGER_ID
    monitoring.use_tool_id(tool, "forge-testrun")
    monitoring.register_callback(tool, monitoring.events.EXCEPTION_HANDLED, handled)
    monitoring.set_events(tool, monitoring.events.EXCEPTION_HANDLED)
try:
    if sys.argv[1] == "-m":
        sys.argv = sys.argv[2:]
        runpy.run_module(sys.argv[0], run_name="__main__", alter_sys=True)
    else:
        sys.argv = sys.argv[1:]
        sys.path[0] = os.path.dirname(os.path.abspath(sys.argv[0]))
        runpy.run_path(sys.argv[0], run_name="__main__")
finally:
    if monitoring is not None:
        monitoring.set_events(tool, 0)
    if caught:
        sys.stderr.write("\n" + MARK + "\n" + "".join(traceback.format_exception(caught[0])))
'''


def caught_exception(argv: list[str], cwd: Path, env: dict[str, str], seconds: float, project: Path) -> str:
    """The traceback of the last exception project code caught itself, from one more run of the command.

    A program that wraps everything in ``except Exception: print(...)`` hides where it failed: the
    repairer sees "an unexpected error occurred" and nothing else. Empty without Python 3.12, or when
    nothing was caught.
    """
    bootstrap = _CAUGHT_BOOTSTRAP.replace("MARK", repr(CAUGHT_MARK))
    _, _, stderr, _ = _run_one([*argv[:3], "-c", bootstrap, str(project), *argv[3:]], cwd, env, seconds,
                               project.parent / "tmp")
    return stderr[stderr.index(CAUGHT_MARK):].strip() if CAUGHT_MARK in stderr else ""


def closed_by_itself(argv: list[str], entry: Path | None, project: Path, result: CommandResult,
                     seconds: float) -> str:
    """Why a game or UI that ended with no input is a bug, or "" when it is not one.

    Started the way its user starts it (no arguments), a program with an event loop must wait for its
    user: ending by itself here means it ends on its user too. A model told that a game "hung" makes
    it quit at game over, after a number of steps, or when it detects a test environment; this catches
    every one of those. Programs given arguments (``--help``) and ones that only draw and exit are left
    alone, so nothing is pushed to run forever that should not.
    """
    plain = len(argv) == (5 if argv[3] == "-m" else 4)
    if not plain or entry is None or result.timed_out or result.crashed or result.skipped:
        return ""
    library = interactive_library(entry, project)
    if not library or not any(_READS_INPUT.search(text) for text in _program_sources(entry, project)):
        return ""
    how = "closed on its own" if result.exit_code == 0 else f"stopped with exit code {result.exit_code}"
    return (f"this {library} program {how} after {seconds:.1f}s with no keyboard or mouse input. A game or UI "
            "must keep running until its user quits; it would close on its user the same way.")


QUIT_ADVICE = ("Look for code that ends it at game over, after a number of steps or frames, or when it detects a "
               "test or headless environment, and remove it")
CAUGHT_ADVICE = ("It closed because of the exception below, which the program caught itself: fix its cause in the "
                 "file and line the traceback ends at")


def _entry_file(argv: list[str], project: Path, folder: str) -> Path | None:
    """The project file a validated command runs (see ``to_argv``)."""
    if argv[3] == "-m":
        module = argv[4].replace(".", "/")
        return next((p for p in (project / f"{module}.py", project / module / "__main__.py") if p.is_file()), None)
    return (project / folder / argv[3]) if folder != "." else project / argv[3]


def classify(result: CommandResult, env: PythonEnv, project: Path, folder: str, entry: Path | None = None) -> None:
    """Mark a crash that only shows a missing service or package as unavailable, not as a bug, and a
    program that runs until stopped (a server, a game, a UI) as started fine rather than hung."""
    if result.timed_out and not result.crashed:
        if _SERVING_TEXT.search(f"{result.stdout}\n{result.stderr}"):
            result.unavailable = "started a server that runs until stopped (it started fine; not a hang)"
            return
        library = interactive_library(entry, project) if entry else ""
        if library:
            result.unavailable = (f"a {library} program that runs until its user quits: it ran without crashing "
                                  "until the time limit, with no keyboard or mouse input (not a hang)")
            return
    if not (result.crashed or result.reported):
        return
    last = _last_error(result.stderr)
    if _SERVICE_ERRORS.search(last) or _SERVICE_TEXT.search(result.stderr):
        result.unavailable = "needs a service that is not running in the test run (database, network)"
        return
    missing = missing_package(result)
    if missing:
        top = missing.split(".")[0]
        base = project / folder
        in_project = any((root / f"{top}.py").is_file() or (root / top).is_dir() for root in (project, base))
        if not in_project and not env.has_dependencies:
            reason = env.warning or "the project has no requirements.txt"
            result.unavailable = f"package '{top}' is not installed in the test run ({reason})"


def _kill_tree(process: subprocess.Popen) -> None:
    """Stop a command and everything it started (Django's autoreloader runs the server in a child process)."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)], capture_output=True,
                           stdin=subprocess.DEVNULL, timeout=15)
        else:
            os.killpg(process.pid, 9)
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        process.kill()
    except OSError:
        pass


def _run_one(argv: list[str], cwd: Path, env: dict[str, str], seconds: float,
             scratch: Path) -> tuple[int | None, str, str, bool]:
    """(exit code, stdout, stderr, timed out). Output goes to files, not pipes: a leftover child process
    holding a pipe open would otherwise block reading it forever."""
    scratch.mkdir(parents=True, exist_ok=True)
    out_path, err_path = scratch / "_testrun_stdout.txt", scratch / "_testrun_stderr.txt"
    extra = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    timed_out, code = False, None
    with open(out_path, "wb") as out, open(err_path, "wb") as err:
        process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=err, **extra)
        try:
            code = process.wait(timeout=seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_tree(process)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
    texts = []
    for path in (out_path, err_path):
        try:
            texts.append(path.read_bytes().decode("utf-8", "replace"))
        except OSError:
            texts.append("")
    return code, texts[0], texts[1], timed_out


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
            started = time.monotonic()
            result.exit_code, result.stdout, result.stderr, result.timed_out = _run_one(
                argv, project / folder, variables, seconds, project.parent / "tmp")
            ran_for = time.monotonic() - started
            result.reported = reported_errors(command, result)
            for attr in ("stdout", "stderr"):
                text = getattr(result, attr).replace(str(project) + os.sep, "").replace(str(project), ".")
                tail = text[-OUTPUT_TAIL:]
                if attr == "stderr" and TRACEBACK in text and TRACEBACK not in tail:
                    tail = f"{TRACEBACK}\n  ... [earlier frames cut]\n{tail}"
                setattr(result, attr, tail)
            entry = _entry_file(argv, project, folder)
            classify(result, env, project, folder, entry)
            if not (result.reported or result.unavailable):
                closed = closed_by_itself(argv, entry, project, result, ran_for)
                if closed:  # show where it failed, when it hid that behind a catch-all
                    caught = caught_exception(argv, project / folder, variables, seconds, project)
                    caught = caught.replace(str(project) + os.sep, "").replace(str(project), ".")[-OUTPUT_TAIL:]
                    if caught:
                        result.stderr = f"{result.stderr.rstrip()}\n{caught}".strip()
                    result.reported = f"{closed} {CAUGHT_ADVICE if caught else QUIT_ADVICE}"
            results.append(result)
    finally:
        shutil.rmtree(project.parent, ignore_errors=True)
    return results


# ------------------------------------------------------------------ import check
_IMPORT_FAILED = "@@FORGE-IMPORT-FAILED@@"
_IMPORT_END = "@@FORGE-IMPORT-END@@"
_SETTINGS_IN_MANAGE = re.compile(r"""DJANGO_SETTINGS_MODULE['"]\s*,\s*['"]([\w.]+)['"]""")
_SKIP_MODULE_NAMES = {"manage.py", "setup.py", "conftest.py", "__main__.py", "wsgi.py", "asgi.py"}

IMPORT_CHECK_SCRIPT = '''import importlib
import os
import sys
import traceback

sys.path.insert(0, os.getcwd())
failed = 0


def report(name):
    global failed
    failed += 1
    print("{failed} " + name, file=sys.stderr)
    traceback.print_exc()
    print("{end}", file=sys.stderr)


{setup}
for name in {modules!r}:
    try:
        importlib.import_module(name)
    except BaseException as exc:
        if isinstance(exc, KeyboardInterrupt):
            raise
        report(name)
{after}
sys.exit(1 if failed else 0)
'''
DJANGO_SETUP = '''os.environ.setdefault("DJANGO_SETTINGS_MODULE", {settings!r})
try:
    import django
    django.setup()
except BaseException:
    report("(django.setup)")
    sys.exit(1)
'''
DJANGO_AFTER = '''try:
    from django.core.management import call_command
    call_command("check")
except BaseException:
    report("(manage.py check, with every module imported)")
'''


@dataclass
class ImportCheck:
    modules: list[str]
    failures: list[dict[str, str]]     # {"module", "path", "detail", "changed"}
    ran: bool = False
    note: str = ""                     # why it did not run, or what it could not judge


def importable_modules(files: set[str], root: str, *, django: bool) -> list[tuple[str, str]]:
    """(dotted module, path) for the project's modules, as imported from ``root``.

    Django: every module under the folder manage.py runs from (Django imports them that way too).
    Other projects: only modules inside packages (folders with __init__.py): a top-level script would
    run its program when imported.
    """
    prefix = "" if root in (".", "") else root.rstrip("/") + "/"
    found: list[tuple[str, str]] = []
    for path in sorted(files):
        if not path.endswith(".py") or not path.startswith(prefix):
            continue
        rel = PurePosixPath(path[len(prefix):])
        if rel.name in _SKIP_MODULE_NAMES or "migrations" in rel.parts or any(
                not part.replace(".py", "").isidentifier() for part in rel.parts):
            continue
        parts = list(rel.with_suffix("").parts)
        if not django:
            if len(parts) < 2:
                continue
            folders = [prefix + "/".join(rel.parts[:i]) for i in range(1, len(rel.parts))]
            if not all(f"{folder}/__init__.py" in files for folder in folders):
                continue
        if parts[-1] == "__init__":
            parts = parts[:-1]
        if parts:
            found.append((".".join(parts), path))
    return found


def _settings_module(changes: ChangeSet, manage: str) -> str | None:
    found = _SETTINGS_IN_MANAGE.search(changes.current(manage) or "")
    return found.group(1) if found else None


def import_check(changes: ChangeSet, env: PythonEnv, seconds: float = 120.0) -> ImportCheck:
    """Import every project module in a throwaway copy (Django: after django.setup(), then its system checks).

    Catches what reading the files cannot: ``from django.setup import setup``, a model defined twice, a
    name a third-party package does not have, code that fails as soon as it is imported.
    """
    files = set(changes.workspace.list_files()) | {c.path for c in changes.changes() if c.action != "delete"}
    for change in changes.changes():
        if change.action == "delete":
            files.discard(change.path)
    manages = sorted(f for f in files if PurePosixPath(f).name == "manage.py")
    django = len(manages) == 1 and any(p.lower().startswith("django") and not p.lower().startswith(
        ("django-", "djangorestframework")) for p in env.packages) and env.has_dependencies
    root = str(PurePosixPath(manages[0]).parent) if django else "."
    settings = _settings_module(changes, manages[0]) if django else None
    if django and not settings:
        return ImportCheck([], [], note="manage.py does not set DJANGO_SETTINGS_MODULE")
    modules = importable_modules(files, root, django=django)
    if not modules:
        return ImportCheck([], [])
    names = [m for m, _ in modules]
    script = IMPORT_CHECK_SCRIPT.format(
        failed=_IMPORT_FAILED, end=_IMPORT_END, modules=names,
        setup=DJANGO_SETUP.format(settings=settings) if django else "",
        after=DJANGO_AFTER if django else "")
    project = build_copy(changes)
    try:
        script_path = project.parent / "tmp" / "_forge_import_check.py"
        script_path.write_text(script, encoding="utf-8")
        argv = [env.python, "-E", "-s", str(script_path)]
        code, out, err, timed_out = _run_one(argv, project / root, sandbox_env(project), seconds,
                                             project.parent / "tmp")
        if timed_out:
            return ImportCheck(names, [], ran=True, note=f"importing the modules did not finish within {seconds:.0f}s")
        text = err.replace(str(project) + os.sep, "").replace(str(project), ".")
    finally:
        shutil.rmtree(project.parent, ignore_errors=True)
    changed = {c.path for c in changes.changes()}
    by_module = dict((m, p) for m, p in modules)
    failures = []
    for block in text.split(_IMPORT_FAILED)[1:]:
        body = block.split(_IMPORT_END)[0]
        name, _, trace = body.partition("\n")
        name = name.strip()
        trace = trace.strip()
        result = CommandResult(command=f"import {name}", exit_code=1, stderr=trace)
        if _SERVICE_TEXT.search(trace) or _SERVICE_ERRORS.search(_last_error(trace)):
            continue  # needs a database or another service while importing: not judged here
        missing = missing_package(result)
        if missing and not env.has_dependencies and not _in_project(missing.split(".")[0], files):
            continue  # a package that is not installed here
        frames = []
        for frame_path, _ in _FRAME.findall(trace):
            rel = frame_path.replace("\\", "/").lstrip("./")
            match = rel if rel in files else next((f for f in files if rel and f.endswith("/" + rel)), "")
            if match:
                frames.append(match)
        system_check = name.startswith("(manage.py check")
        culprit = _dotted_culprit(trace, files) if system_check else ""
        path = (frames[-1] if frames else culprit or by_module.get(name, manages[0] if manages else ""))
        involved = set(frames) | ({by_module[name]} if name in by_module else set()) | ({culprit} if culprit else set())
        if system_check and any(c.endswith(".py") for c in changed):
            involved |= changed  # the system checks judge the whole project
        last = _last_error(trace)
        tail = "\n".join(trace.splitlines()[-8:])
        failures.append({
            "module": name, "path": path, "changed": "yes" if involved & changed else "",
            "detail": f"importing {name} fails: {last}\n{tail}" if not name.startswith("(") else
                      f"{name} fails: {last}\n{tail}",
        })
    return ImportCheck(names, failures, ran=True)


def project_app_labels(changes: ChangeSet, root: str, files: set[str]) -> list[str]:
    """Labels of the project's own Django apps that have models, from INSTALLED_APPS."""
    import ast

    labels: list[str] = []
    for path in sorted(files):
        if not path.endswith(".py"):
            continue
        source = changes.current(path) or ""
        if "INSTALLED_APPS" not in source or "ROOT_URLCONF" not in source:
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        for node in tree.body:
            if not (isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "INSTALLED_APPS" for t in node.targets)):
                continue
            for const in ast.walk(node.value):
                if not (isinstance(const, ast.Constant) and isinstance(const.value, str)):
                    continue
                module = const.value.split(".apps.")[0] if ".apps." in const.value else const.value
                stem = module.replace(".", "/")
                models = f"{stem}/models.py" if root == "." else f"{root}/{stem}/models.py"
                package = f"{stem}/models/__init__.py" if root == "." else f"{root}/{stem}/models/__init__.py"
                label = module.split(".")[-1]
                if (models in files or package in files) and label not in labels:
                    labels.append(label)
    return labels


def generate_migrations(changes: ChangeSet, env: PythonEnv, seconds: float = 90.0) -> tuple[dict[str, str], str]:
    """({path: content} of new migration files, error) from Django's ``makemigrations`` on the proposed code.

    Runs in a throwaway copy, like the test run. Needs Django among the installed requirements;
    otherwise nothing happens. Model errors come back as the error (the test run reports them too).
    """
    if not env.has_dependencies or not any(p.lower().startswith("django") and not p.lower().startswith(
            ("django-", "djangorestframework")) for p in env.packages):
        return {}, ""
    files = set(changes.workspace.list_files()) | {c.path for c in changes.changes() if c.action != "delete"}
    manages = sorted(f for f in files if PurePosixPath(f).name == "manage.py")
    if len(manages) != 1:
        return {}, ""
    folder = str(PurePosixPath(manages[0]).parent)
    labels = project_app_labels(changes, folder, files)
    if not labels:
        return {}, ""
    project = build_copy(changes)
    try:
        before = {p.relative_to(project).as_posix() for p in project.rglob("*") if p.is_file()}
        # Apps without a migrations package only get their first migration when named explicitly.
        argv = [env.python, "-E", "-s", "manage.py", "makemigrations", "--noinput", *labels]
        code, out, err, timed_out = _run_one(argv, project / folder, sandbox_env(project), seconds,
                                             project.parent / "tmp")
        if timed_out:
            return {}, f"makemigrations did not finish within {seconds:.0f}s"
        if code != 0:
            text = (err or out).replace(str(project) + os.sep, "").strip()
            return {}, "\n".join(text.splitlines()[-12:])
        created: dict[str, str] = {}
        for path in project.rglob("*.py"):
            rel = path.relative_to(project).as_posix()
            if rel not in before and "migrations" in PurePosixPath(rel).parts:
                created[rel] = path.read_text(encoding="utf-8")
        return created, ""
    finally:
        shutil.rmtree(project.parent, ignore_errors=True)


def _dotted_culprit(text: str, project_files: set[str]) -> str:
    """Django names the culprit by module: "<class 'menu.admin.MenuItemAdmin'>" -> menu/admin.py."""
    for dotted in _DOTTED.findall(text):
        parts = dotted.split(".")
        for end in range(len(parts), 1, -1):
            suffix = "/".join(parts[:end]) + ".py"
            match = next((f for f in sorted(project_files) if f == suffix or f.endswith("/" + suffix)), "")
            if match:
                return match
    return ""


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
        missing = missing_package(result)
        if missing and _in_project(missing.split(".")[0], project_files):
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
            path = _dotted_culprit(result.stderr, project_files)
        if not path:
            tokens = result.command.split()
            script = tokens[1] if len(tokens) > 1 and tokens[1] != "-m" else ""
            folder = result.ran_as[3:].split(" && ")[0] if result.ran_as.startswith("cd ") else ""
            if script and folder:
                script = f"{folder}/{script}"
            path = script or (changes.paths() or ["?"])[0]
        what = (f"hung: no exit within {seconds:.0f}s" if result.timed_out else
                "crashed with a traceback" if result.crashed and CAUGHT_MARK not in result.stderr else
                f"failed: {result.reported}")
        if missing and requirements and path in requirements:
            what += f" (package '{missing.split('.')[0]}' is imported but not listed in {path})"
        output = result.stderr if result.stderr.strip() else result.stdout
        tail = "\n".join(output.strip().splitlines()[-12:])
        ran = f" (run as `{result.ran_as}`)" if result.ran_as else ""
        problems.append({
            "path": path, "check": "test-run",
            "detail": f"`{result.command}`{ran} {what}" + (f"\n{tail}" if tail else ""),
        })
    return problems
