"""Facts about a project, read straight from its files: dependencies, Django routes, frontend API
calls, environment variables and layout.

The gate checks (checks.py), the automatic fixes (autofix.py), the project brief (projectdocs.py)
and the context specialists get all use these. Nothing is executed and no model is asked, so the
facts are only as good as the parsing: when something cannot be read with certainty (a computed
URL, a view class from outside the project) it is left out or marked unknown, never guessed.
"""

from __future__ import annotations

import ast
import fnmatch
import json
import re
import sys
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Callable, Iterable

from . import jsinterface
from .pyinterface import _candidates, _project_root_of, top_level_names
from .workspace import IGNORED_DIRS

Reader = Callable[[str], "str | None"]
# Folders whose contents are generated or installed (dist/, node_modules/ ...), never checked.
IGNORED_PARTS = IGNORED_DIRS


def join(folder: str | PurePosixPath, name: str) -> str:
    folder = str(folder)
    return name if folder in ("", ".") else f"{folder.rstrip('/')}/{name}"


def ancestors(path: str) -> list[str]:
    """The folders containing ``path``, nearest first, ending with "." (the project root)."""
    parent = PurePosixPath(path).parent
    return [str(p) for p in (parent, *parent.parents)]


# ------------------------------------------------------------------ project view
class ProjectView:
    """The project as the team currently sees it: workspace files plus proposed changes. Parses are cached."""

    def __init__(self, files: Iterable[str], read: Reader) -> None:
        self.files = set(files)
        self._read = read
        self._text: dict[str, str | None] = {}
        self._trees: dict[str, ast.Module | None] = {}
        py_dirs = {str(p) + "/" for f in self.files if f.endswith(".py") for p in PurePosixPath(f).parents
                   if str(p) != "."}
        self._py_dirs = py_dirs

    @classmethod
    def of(cls, changes) -> "ProjectView":
        files = set(changes.workspace.list_files())
        for change in changes.changes():
            if change.action == "delete":
                files.discard(change.path)
            else:
                files.add(change.path)
        return cls(files, changes.current)

    def read(self, path: str) -> str | None:
        if path not in self._text:
            self._text[path] = self._read(path) if path in self.files else None
        return self._text[path]

    def tree(self, path: str) -> ast.Module | None:
        if path not in self._trees:
            source = self.read(path)
            try:
                self._trees[path] = ast.parse(source) if source is not None else None
            except (SyntaxError, ValueError):
                self._trees[path] = None
        return self._trees[path]

    def exists(self, path: str) -> bool:
        """A file, or (asked as ``"folder/"``) a folder holding Python files: what pyinterface expects."""
        return path in self.files or path in self._py_dirs

    def definitions(self, path: str) -> set[str]:
        """Names a module itself defines at top level (classes, functions, assignments), not ones it imports."""
        tree = self.tree(path)
        if tree is None:
            return set()
        imported = {(a.asname or a.name).split(".")[0] for n in ast.walk(tree)
                    if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}
        return top_level_names(tree) - imported

    def has_dir(self, folder: str) -> bool:
        prefix = folder.rstrip("/") + "/"
        return any(f.startswith(prefix) for f in self.files)

    def named(self, *names: str) -> list[str]:
        wanted = {n.lower() for n in names}
        return sorted(f for f in self.files if PurePosixPath(f).name.lower() in wanted)

    def matching(self, *patterns: str) -> list[str]:
        return sorted(f for f in self.files if any(fnmatch.fnmatch(PurePosixPath(f).name, p) for p in patterns))


def top_level_definitions(view: ProjectView, path: str) -> set[str]:
    return view.definitions(path)


# ------------------------------------------------------------------ Python dependencies
STDLIB =frozenset(sys.stdlib_module_names) | {"__future__", "__main__"}

# Import name -> the distribution pip installs. Names not listed are assumed to match their import.
IMPORT_TO_DIST = {
    "rest_framework": "djangorestframework", "rest_framework_simplejwt": "djangorestframework-simplejwt",
    "corsheaders": "django-cors-headers", "django_filters": "django-filter", "environ": "django-environ",
    "dj_database_url": "dj-database-url", "debug_toolbar": "django-debug-toolbar", "storages": "django-storages",
    "allauth": "django-allauth", "drf_spectacular": "drf-spectacular", "drf_yasg": "drf-yasg",
    "django_extensions": "django-extensions", "django_redis": "django-redis", "django_celery_beat":
    "django-celery-beat", "import_export": "django-import-export", "crispy_forms": "django-crispy-forms",
    "psycopg": "psycopg[binary]", "psycopg2": "psycopg2-binary", "yaml": "PyYAML", "PIL": "Pillow",
    "dotenv": "python-dotenv", "jwt": "PyJWT", "bs4": "beautifulsoup4", "sklearn": "scikit-learn",
    "cv2": "opencv-python", "dateutil": "python-dateutil", "multipart": "python-multipart",
    "jose": "python-jose", "attr": "attrs", "Crypto": "pycryptodome", "OpenSSL": "pyOpenSSL",
    "serial": "pyserial", "docx": "python-docx", "pptx": "python-pptx", "fitz": "PyMuPDF",
    "slugify": "python-slugify", "socketio": "python-socketio", "telegram": "python-telegram-bot",
    "MySQLdb": "mysqlclient", "pymysql": "PyMySQL", "jinja2": "Jinja2", "markdown": "Markdown",
    "pydantic_settings": "pydantic-settings", "flask_sqlalchemy": "Flask-SQLAlchemy", "flask_cors": "Flask-Cors",
    "flask_login": "Flask-Login", "flask_migrate": "Flask-Migrate", "flask_wtf": "Flask-WTF",
    "sqlalchemy": "SQLAlchemy", "zmq": "pyzmq", "googleapiclient": "google-api-python-client", "grpc": "grpcio",
    "skimage": "scikit-image", "win32api": "pywin32", "win32con": "pywin32", "pythoncom": "pywin32",
    "pkg_resources": "setuptools", "magic": "python-magic", "decouple": "python-decouple",
}
# Distributions whose import name is the distribution name: safe to add to requirements.txt unasked.
COMMON_DISTS = frozenset({
    "django", "flask", "fastapi", "requests", "httpx", "numpy", "pandas", "pydantic", "click", "rich", "pytest",
    "boto3", "stripe", "openai", "anthropic", "aiohttp", "uvicorn", "gunicorn", "whitenoise", "celery", "redis",
    "alembic", "matplotlib", "scipy", "tqdm", "channels", "daphne", "waitress", "lxml", "starlette", "typer",
    "websockets", "selenium", "playwright", "paramiko", "cryptography", "bcrypt", "passlib", "marshmallow",
    "arrow", "pendulum", "orjson", "ujson", "tenacity", "loguru", "structlog", "sentry_sdk", "hypercorn",
    "werkzeug", "itsdangerous", "markupsafe", "psutil", "schedule", "apscheduler", "pymongo", "motor",
    "elasticsearch", "kafka", "pika", "nats", "minio", "faker", "factory", "freezegun", "responses",
})
# Packages that come with another one: importing them without listing them is fine when the parent is listed.
TRANSITIVE = {
    "asgiref": {"django"}, "sqlparse": {"django"}, "starlette": {"fastapi"}, "pydantic": {"fastapi"},
    "werkzeug": {"flask"}, "jinja2": {"flask"}, "click": {"flask", "typer"}, "itsdangerous": {"flask"},
    "markupsafe": {"jinja2", "flask"}, "urllib3": {"requests"}, "certifi": {"requests", "httpx"},
    "idna": {"requests", "httpx"}, "anyio": {"httpx", "starlette", "fastapi"}, "kombu": {"celery"},
    "typing_extensions": {"pydantic", "fastapi", "sqlalchemy"}, "h11": {"uvicorn", "httpx"},
}
# Alternatives that satisfy one import.
ALTERNATIVES = {
    "psycopg": {"psycopg", "psycopg-binary", "psycopg-c"}, "psycopg2": {"psycopg2", "psycopg2-binary"},
    "cv2": {"opencv-python", "opencv-python-headless", "opencv-contrib-python"},
    "Crypto": {"pycryptodome", "pycrypto"}, "MySQLdb": {"mysqlclient"}, "jose": {"python-jose"},
}
# Top-level names that are namespaces shared by many distributions: too ambiguous to check.
NAMESPACE_TOPS = frozenset({"google", "azure", "mpl_toolkits", "_pytest", "pytest_django"})
SERVER_TOOLS = ("gunicorn", "uvicorn", "daphne", "hypercorn", "celery", "waitress-serve")


def normalize_dist(name: str) -> str:
    name = re.split(r"[\[;<>=!~ ]", name.strip(), maxsplit=1)[0]
    return re.sub(r"[-_.]+", "-", name).lower()


def dist_for(top: str) -> str:
    """The requirement line to add for an import."""
    return IMPORT_TO_DIST.get(top, top)


def satisfies(top: str, listed: set[str]) -> bool:
    """Whether requirements (normalized names) cover importing ``top``."""
    wanted = {normalize_dist(top), normalize_dist(dist_for(top))} | {normalize_dist(a) for a in ALTERNATIVES.get(top, ())}
    if wanted & listed:
        return True
    return any(normalize_dist(parent) in listed for parent in TRANSITIVE.get(top, ()))


def auto_addable(top: str) -> bool:
    return top in IMPORT_TO_DIST or top.lower() in COMMON_DISTS


_REQ_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


def requirement_lines(text: str) -> set[str]:
    names = set()
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        match = _REQ_NAME.match(line)
        if match:
            names.add(normalize_dist(match.group(1)))
    return names


def _pyproject_names(text: str) -> set[str] | None:
    try:
        import tomllib
    except ImportError:  # Python < 3.11
        return None
    try:
        data = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, ValueError):
        return None
    project = data.get("project") or {}
    entries = list(project.get("dependencies") or [])
    for group in (project.get("optional-dependencies") or {}).values():
        entries += list(group or [])
    poetry = ((data.get("tool") or {}).get("poetry") or {})
    names = {normalize_dist(e) for e in entries if isinstance(e, str)}
    for section in ("dependencies", "dev-dependencies"):
        names |= {normalize_dist(k) for k in (poetry.get(section) or {}) if k.lower() != "python"}
    for group in (poetry.get("group") or {}).values():
        names |= {normalize_dist(k) for k in ((group or {}).get("dependencies") or {})}
    if not names and "project" not in data and "poetry" not in (data.get("tool") or {}):
        return None  # a pyproject.toml that only configures tools
    return names


def python_manifest(view: ProjectView, path: str) -> str | None:
    """The requirements file covering a Python file: the nearest requirements.txt or pyproject.toml above it."""
    for folder in ancestors(path):
        requirements = join(folder, "requirements.txt")
        if requirements in view.files:
            return requirements
        pyproject = join(folder, "pyproject.toml")
        if pyproject in view.files and _pyproject_names(view.read(pyproject) or "") is not None:
            return pyproject
    return None


def manifest_names(view: ProjectView, manifest: str) -> set[str]:
    if manifest.endswith("pyproject.toml"):
        return _pyproject_names(view.read(manifest) or "") or set()
    folder = str(PurePosixPath(manifest).parent)
    names: set[str] = set()
    seen: set[str] = set()
    todo = [f for f in view.files if str(PurePosixPath(f).parent) == folder
            and fnmatch.fnmatch(PurePosixPath(f).name.lower(), "requirements*.txt")]
    while todo:
        current = todo.pop()
        if current in seen:
            continue
        seen.add(current)
        text = view.read(current) or ""
        names |= requirement_lines(text)
        for include in re.findall(r"(?m)^\s*-r\s+(\S+)", text):
            todo.append(join(PurePosixPath(current).parent, include))
    return names


def _optional_lines(tree: ast.Module) -> set[int]:
    """Lines inside ``try:`` blocks that handle ImportError, and ``if TYPE_CHECKING:`` blocks."""
    lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            names = {ast.unparse(h.type) for h in node.handlers if h.type is not None}
            if any("ImportError" in n or "ModuleNotFoundError" in n or n == "Exception" for n in names) \
                    or any(h.type is None for h in node.handlers):
                for stmt in node.body:
                    lines.update(range(stmt.lineno, (stmt.end_lineno or stmt.lineno) + 1))
        elif isinstance(node, ast.If) and "TYPE_CHECKING" in ast.unparse(node.test):
            for stmt in node.body:
                lines.update(range(stmt.lineno, (stmt.end_lineno or stmt.lineno) + 1))
    return lines


def is_project_module(view: ProjectView, module: str, importer: str) -> bool:
    if any(view.exists(c) for c in _candidates(module, importer, 0)):
        return True
    return _project_root_of(module, importer, view.exists) is not None


def third_party_imports(view: ProjectView, path: str) -> list[tuple[str, int]]:
    """(top-level package, line) for each import of something outside the project and the standard library."""
    tree = view.tree(path)
    if tree is None:
        return []
    optional = _optional_lines(tree)
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules = [node.module]
        else:
            continue
        if node.lineno in optional:
            continue
        for module in modules:
            top = module.split(".")[0]
            if top in STDLIB or top in NAMESPACE_TOPS or is_project_module(view, module, path):
                continue
            found.append((top, node.lineno))
    return found


# Database engines need a driver package.
_ENGINE_DRIVERS = {
    "postgresql": ("psycopg", "psycopg[binary]"), "postgis": ("psycopg", "psycopg[binary]"),
    "mysql": ("MySQLdb", "mysqlclient"), "oracle": ("oracledb", "oracledb"),
}


def settings_imports(view: ProjectView, path: str) -> list[tuple[str, int]]:
    """Packages a Django settings module loads by name: apps, middleware, backends, and database drivers."""
    tree = view.tree(path)
    if tree is None or not any(isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "INSTALLED_APPS"
                                                                  for t in n.targets) for n in tree.body):
        return []
    found: list[tuple[str, int]] = []

    def add(dotted: str, line: int) -> None:
        if not re.fullmatch(r"[A-Za-z_]\w*(\.[A-Za-z_]\w*)+|[A-Za-z_]\w*", dotted):
            return
        top = dotted.split(".")[0]
        if top in STDLIB or is_project_module(view, dotted.rsplit(".", 1)[0] if "." in dotted else dotted, path) \
                or is_project_module(view, top, path):
            return
        found.append((top, line))

    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        names = {getattr(t, "id", "") for t in node.targets}
        if names & {"INSTALLED_APPS", "MIDDLEWARE"}:
            for const in ast.walk(node.value):
                if isinstance(const, ast.Constant) and isinstance(const.value, str):
                    add(const.value, const.lineno)
        elif names & {"STATICFILES_STORAGE", "DEFAULT_FILE_STORAGE"} and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            add(node.value.value, node.lineno)
        else:
            for sub in ast.walk(node.value):
                if not isinstance(sub, ast.Dict):
                    continue
                for key, value in zip(sub.keys, sub.values):
                    key_name = key.value if isinstance(key, ast.Constant) else ""
                    if not isinstance(key_name, str):
                        continue
                    if key_name in ("BACKEND", "ENGINE") or key_name.startswith("DEFAULT_"):
                        for const in ast.walk(value):
                            if isinstance(const, ast.Constant) and isinstance(const.value, str):
                                if key_name == "ENGINE" and const.value.startswith("django.db.backends."):
                                    engine = const.value.rsplit(".", 1)[-1]
                                    if engine in _ENGINE_DRIVERS:
                                        found.append((_ENGINE_DRIVERS[engine][0], const.lineno))
                                    continue
                                add(const.value, const.lineno)
    return found


_TOOL_USE = re.compile(r"(?<![\w/.-])(" + "|".join(re.escape(t) for t in SERVER_TOOLS) + r")(?=[\s\"'\],]|$)")


def server_tool_uses(view: ProjectView) -> list[tuple[str, str, int]]:
    """(tool, file, line) for servers started from Dockerfiles, scripts and process configs (gunicorn ...)."""
    found = []
    for path in sorted(view.files):
        name = PurePosixPath(path).name.lower()
        if not (name.startswith("dockerfile") or name.endswith((".sh", ".conf", ".ini", ".dockerfile"))
                or name in ("procfile",) or (name.startswith("docker-compose") and name.endswith((".yml", ".yaml")))
                or name.startswith("compose.")):
            continue
        text = view.read(path) or ""
        for number, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#") or re.search(r"\b(pip3?|python3? -m pip|apt-get|apk|uv pip) (install|add)\b",
                                                     stripped):
                continue
            for match in _TOOL_USE.finditer(line):
                found.append((match.group(1).split("-")[0], path, number))
    return found


def pip_installs_in_docker(view: ProjectView) -> set[str]:
    """Packages a Dockerfile installs by name (``pip install gunicorn``), so requirements need not list them."""
    names: set[str] = set()
    for path in view.files:
        if PurePosixPath(path).name.lower().startswith("dockerfile") or path.lower().endswith(".dockerfile"):
            for match in re.finditer(r"pip3?\s+install\s+([^\n&|;\\]+)", view.read(path) or ""):
                names |= {normalize_dist(t) for t in match.group(1).split() if not t.startswith("-")}
    return names


# ------------------------------------------------------------------ JavaScript dependencies
NODE_BUILTINS = frozenset({
    "fs", "path", "url", "http", "https", "os", "crypto", "stream", "util", "events", "child_process", "zlib",
    "buffer", "assert", "net", "tls", "dns", "querystring", "readline", "worker_threads", "module", "process",
    "timers", "vm", "perf_hooks", "string_decoder", "punycode", "cluster", "dgram", "inspector", "v8", "tty",
})
# Versions known to exist and work together; anything else gets "latest".
KNOWN_JS_VERSIONS = {
    "react": "^18.3.1", "react-dom": "^18.3.1", "react-router-dom": "^6.26.2", "react-router": "^6.26.2",
    "axios": "^1.7.7", "vite": "^5.4.8", "@vitejs/plugin-react": "^4.3.2", "@vitejs/plugin-react-swc": "^3.7.1",
    "@vitejs/plugin-vue": "^5.1.4", "vue": "^3.5.11", "vue-router": "^4.4.5", "pinia": "^2.2.4",
    "@tanstack/react-query": "^5.59.0", "zustand": "^4.5.5", "react-redux": "^9.1.2", "@reduxjs/toolkit": "^2.2.7",
    "clsx": "^2.1.1", "lucide-react": "^0.447.0", "react-icons": "^5.3.0", "framer-motion": "^11.11.1",
    "tailwindcss": "^3.4.13", "postcss": "^8.4.47", "autoprefixer": "^10.4.20", "typescript": "^5.6.2",
    "@types/react": "^18.3.11", "@types/react-dom": "^18.3.0", "vitest": "^2.1.2", "date-fns": "^4.1.0",
    "dayjs": "^1.11.13", "uuid": "^10.0.0", "prop-types": "^15.8.1", "react-hot-toast": "^2.4.1", "swr": "^2.2.5",
    "recharts": "^2.12.7", "chart.js": "^4.4.4", "react-chartjs-2": "^5.2.0", "sass": "^1.79.4",
    "@testing-library/react": "^16.0.1", "@testing-library/jest-dom": "^6.5.0", "jsdom": "^25.0.1",
}
DEV_JS_PACKAGES = frozenset({
    "vite", "@vitejs/plugin-react", "@vitejs/plugin-react-swc", "@vitejs/plugin-vue", "typescript", "tailwindcss",
    "postcss", "autoprefixer", "eslint", "prettier", "vitest", "sass", "less", "jsdom",
    "@testing-library/react", "@testing-library/jest-dom", "jest",
})
_REQUIRE = re.compile(r"""\brequire\s*\(\s*['"]([^'"]+)['"]\s*\)""")
_DYNAMIC_IMPORT = re.compile(r"""\bimport\s*\(\s*['"]([^'"]+)['"]\s*\)""")


def package_name(spec: str) -> str | None:
    """'react-dom/client' -> 'react-dom'; '@scope/pkg/x' -> '@scope/pkg'; relative, aliases and builtins -> None."""
    if not spec or spec.startswith((".", "/", "~", "#", "node:", "virtual:", "data:", "http:", "https:", "\\0")):
        return None
    if spec.startswith("@"):
        parts = spec.split("/")
        if len(parts) < 2 or not parts[0][1:]:
            return None  # '@/components': a path alias
        return "/".join(parts[:2])
    name = spec.split("/")[0]
    return None if name in NODE_BUILTINS or name.startswith("$") else name


def js_packages(view: ProjectView, path: str) -> list[tuple[str, int]]:
    """(package, line) for each package a JS/TS file imports."""
    text = view.read(path)
    if text is None:
        return []
    stripped = jsinterface._strip_comments(text)
    specs: list[tuple[str, int]] = []
    for regex, group in ((jsinterface._IMPORT, "src"), (jsinterface._SIDE_EFFECT_IMPORT, "src"),
                         (jsinterface._REEXPORT, "src")):
        specs += [(m.group(group), stripped.count("\n", 0, m.start()) + 1) for m in regex.finditer(stripped)]
    for regex in (_REQUIRE, _DYNAMIC_IMPORT):
        specs += [(m.group(1), stripped.count("\n", 0, m.start()) + 1) for m in regex.finditer(stripped)]
    aliases = vite_aliases(view, path)
    found = []
    for spec, line in specs:
        if any(spec == a or spec.startswith(a + "/") for a in aliases):
            continue
        name = package_name(spec)
        if name:
            found.append((name, line))
    if PurePosixPath(path).name.startswith("postcss.config"):
        block = re.search(r"plugins\s*:\s*\{([^}]*)\}", stripped)
        if block:
            for key in re.findall(r"['\"]?([@\w/-]+)['\"]?\s*:", block.group(1)):
                found.append((key, 1))
    return found


def vite_config(view: ProjectView, folder: str) -> str | None:
    for name in ("vite.config.js", "vite.config.ts", "vite.config.mjs", "vite.config.cjs", "vite.config.mts"):
        candidate = join(folder, name)
        if candidate in view.files:
            return candidate
    return None


def vite_aliases(view: ProjectView, path: str) -> set[str]:
    for folder in ancestors(path):
        config = vite_config(view, folder)
        if config:
            text = view.read(config) or ""
            block = re.search(r"alias\s*:\s*[\[{](.*?)[\]}]\s*[,}]", text, re.S)
            if not block:
                return set()
            keys = set(re.findall(r"['\"]([@~#$\w/-]+)['\"]\s*:", block.group(1)))
            keys |= set(re.findall(r"find\s*:\s*['\"]([^'\"]+)['\"]", block.group(1)))
            return keys
    return set()


def js_root(view: ProjectView, path: str) -> str:
    """The folder a JS file's package.json belongs in: the nearest one with package.json, else with a
    Vite config or index.html, else the file's top-level folder."""
    folders = ancestors(path)
    for folder in folders:
        if join(folder, "package.json") in view.files:
            return folder
    for folder in folders:
        if vite_config(view, folder) or join(folder, "index.html") in view.files:
            return folder
    parts = PurePosixPath(path).parts
    return parts[0] if len(parts) > 1 else "."


def package_json(view: ProjectView, folder: str) -> dict | None:
    """The parsed package.json in ``folder``; None when missing, {} when it does not parse."""
    text = view.read(join(folder, "package.json"))
    if text is None:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def package_deps(data: dict) -> set[str]:
    names: set[str] = set()
    for key in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        section = data.get(key)
        if isinstance(section, dict):
            names |= set(section)
    return names


def build_output_dir(view: ProjectView, folder: str) -> str | None:
    """Where ``npm run build`` writes in ``folder``: Vite's outDir (default dist), build for Create React App."""
    config = vite_config(view, folder)
    if config:
        found = re.search(r"outDir\s*:\s*['\"]([^'\"]+)['\"]", view.read(config) or "")
        return found.group(1).strip("./") if found else "dist"
    data = package_json(view, folder) or {}
    if "react-scripts" in package_deps(data):
        return "build"
    return None


# ------------------------------------------------------------------ Django routes
@dataclass
class Route:
    path: str                              # "/api/orders/{}/"; {} is one path segment
    methods: frozenset[str] | None         # None: not known, any method may work
    source: str                            # the urls file that declares it
    view: str = ""
    view_source: str = ""
    catch_all: bool = False                # matches arbitrary paths (an SPA fallback)
    prefix_only: bool = False              # a subtree we cannot see into (admin, a third-party include)
    pattern: str = ""                      # regex for the whole path

    def matches(self, path: str) -> bool:
        probe = _probe(path)
        if self.prefix_only:
            prefix = self.path.rstrip("/")
            return probe == prefix or probe.startswith(prefix + "/")
        return re.fullmatch(self.pattern, probe) is not None


def _probe(path: str) -> str:
    """A concrete path for matching: unknown segments become a sample value, trailing slashes dropped."""
    return re.sub(r"\{\}", "1", path).rstrip("/") or "/"


_CONVERTER = re.compile(r"<(?:(\w+):)?(\w+)>")
VIEWSET_ACTIONS = {
    "ModelViewSet": {"list", "create", "retrieve", "update", "partial_update", "destroy"},
    "ReadOnlyModelViewSet": {"list", "retrieve"},
    "GenericViewSet": set(), "ViewSet": set(), "ViewSetMixin": set(),
    "ListModelMixin": {"list"}, "CreateModelMixin": {"create"}, "RetrieveModelMixin": {"retrieve"},
    "UpdateModelMixin": {"update", "partial_update"}, "DestroyModelMixin": {"destroy"},
}
GENERIC_VIEW_METHODS = {
    "ListAPIView": {"GET"}, "CreateAPIView": {"POST"}, "ListCreateAPIView": {"GET", "POST"},
    "RetrieveAPIView": {"GET"}, "UpdateAPIView": {"PUT", "PATCH"}, "DestroyAPIView": {"DELETE"},
    "RetrieveUpdateAPIView": {"GET", "PUT", "PATCH"}, "RetrieveDestroyAPIView": {"GET", "DELETE"},
    "RetrieveUpdateDestroyAPIView": {"GET", "PUT", "PATCH", "DELETE"},
    "ObtainAuthToken": {"POST"}, "TokenObtainPairView": {"POST"}, "TokenRefreshView": {"POST"},
    "TokenVerifyView": {"POST"}, "LoginView": {"GET", "POST"}, "LogoutView": {"GET", "POST"},
    "TemplateView": {"GET"}, "RedirectView": {"GET", "POST", "PUT", "PATCH", "DELETE"},
    "APIView": set(), "View": set(), "GenericAPIView": set(),
}
FUNCTION_VIEW_METHODS = {"obtain_auth_token": {"POST"}, "serve": {"GET"}}
_HTTP = ("get", "post", "put", "patch", "delete")
_ROUTER_CLASSES = {"DefaultRouter", "SimpleRouter", "ExtendedSimpleRouter", "NestedSimpleRouter"}


def _call_name(node: ast.AST) -> str:
    func = node.func if isinstance(node, ast.Call) else node
    return getattr(func, "id", None) or getattr(func, "attr", "") or ""


def _const(node: ast.AST | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _imports(tree: ast.Module) -> dict[str, tuple[str, str, int]]:
    """local name -> (module, attribute or "", level)."""
    names: dict[str, tuple[str, str, int]] = {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names[alias.asname or alias.name] = (node.module or "", alias.name, node.level)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names[alias.asname or alias.name.split(".")[0]] = (alias.name if alias.asname else
                                                                   alias.name.split(".")[0], "", 0)
    return names


def resolve_module(view: ProjectView, module: str, importer: str, level: int = 0) -> str | None:
    return next((c for c in _candidates(module, importer, level) if c in view.files), None)


@dataclass
class _ClassInfo:
    actions: set[str]
    methods: set[str]
    extra: list[tuple[bool, str, frozenset[str]]]   # (detail, url_path, methods) from @action
    known: bool
    source: str


def _find_definition(view: ProjectView, name: str, importer: str) -> tuple[str, ast.AST] | None:
    """The project file and def/class node a name in ``importer`` refers to."""
    tree = view.tree(importer)
    if tree is None:
        return None
    base, _, attr = name.partition(".")
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return importer, node
    imports = _imports(tree)
    if attr and base in imports:  # views.order_list
        module, imported, level = imports[base]
        target = resolve_module(view, f"{module}.{imported}" if imported and module else (module or imported),
                                importer, level)
        if target is None and imported:
            target = resolve_module(view, imported if level else f"{module}.{imported}", importer, level)
        lookup = attr
    elif name in imports:
        module, imported, level = imports[name]
        target = resolve_module(view, module, importer, level)
        lookup = imported or name
    else:
        return None
    target_tree = view.tree(target) if target else None
    if target_tree is None:
        return None
    for node in target_tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == lookup:
            return target, node
    return None


def _class_info(view: ProjectView, name: str, importer: str, depth: int = 0) -> _ClassInfo:
    found = _find_definition(view, name, importer)
    simple = name.rsplit(".", 1)[-1]
    if found is None or not isinstance(found[1], ast.ClassDef):
        if simple in VIEWSET_ACTIONS:
            return _ClassInfo(set(VIEWSET_ACTIONS[simple]), set(), [], True, "")
        if simple in GENERIC_VIEW_METHODS:
            return _ClassInfo(set(), set(GENERIC_VIEW_METHODS[simple]), [], True, "")
        return _ClassInfo(set(), set(), [], False, "")
    source, node = found
    defined = {n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    for stmt in node.body:
        if isinstance(stmt, ast.Assign):
            defined |= {t.id for t in stmt.targets if isinstance(t, ast.Name)}
    info = _ClassInfo(defined & {"list", "create", "retrieve", "update", "partial_update", "destroy"},
                      {m.upper() for m in defined & set(_HTTP)}, [], True, source)
    for fn in node.body:
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in fn.decorator_list:
            if isinstance(decorator, ast.Call) and _call_name(decorator) == "action":
                keywords = {k.arg: k.value for k in decorator.keywords}
                detail = isinstance(keywords.get("detail"), ast.Constant) and bool(keywords["detail"].value)
                url_path = _const(keywords.get("url_path")) or fn.name
                methods = keywords.get("methods")
                verbs = {(_const(e) or "").upper() for e in getattr(methods, "elts", [])} or {"GET"}
                info.extra.append((detail, url_path, frozenset(verbs)))
    for base in node.bases:
        base_name = ast.unparse(base)
        simple_base = base_name.rsplit(".", 1)[-1]
        if simple_base in VIEWSET_ACTIONS:
            info.actions |= VIEWSET_ACTIONS[simple_base]
        elif simple_base in GENERIC_VIEW_METHODS:
            info.methods |= GENERIC_VIEW_METHODS[simple_base]
        elif depth < 3:
            parent = _class_info(view, base_name, source, depth + 1)
            info.actions |= parent.actions
            info.methods |= parent.methods
            info.extra += parent.extra
            info.known = info.known and parent.known
        else:
            info.known = False
    return info


def _function_methods(view: ProjectView, name: str, importer: str) -> frozenset[str] | None:
    simple = name.rsplit(".", 1)[-1]
    if simple in FUNCTION_VIEW_METHODS:
        return frozenset(FUNCTION_VIEW_METHODS[simple])
    found = _find_definition(view, name, importer)
    if found is None or isinstance(found[1], ast.ClassDef):
        return None
    for decorator in found[1].decorator_list:  # type: ignore[union-attr]
        dname = _call_name(decorator) if isinstance(decorator, ast.Call) else getattr(decorator, "id", "")
        if dname in ("api_view", "require_http_methods") and isinstance(decorator, ast.Call) and decorator.args:
            verbs = {(_const(e) or "").upper() for e in getattr(decorator.args[0], "elts", [])}
            return frozenset(v for v in verbs if v) or frozenset({"GET"})
        if dname == "api_view":
            return frozenset({"GET"})
        if dname in ("require_GET", "require_safe"):
            return frozenset({"GET"})
        if dname == "require_POST":
            return frozenset({"POST"})
    return None


def _path_regex(route: str, is_regex: bool) -> tuple[str, str, bool]:
    """(display path piece, regex piece, catch-all) for one path()/re_path() route string."""
    if is_regex:
        body = route[1:] if route.startswith("^") else route
        body = body[:-1] if body.endswith("$") else body
        display = re.sub(r"\(\?P<\w+>[^)]*\)|\([^)?][^)]*\)", "{}", body)
        catch_all = ".*" in body or ".+" in body.replace("[^/]+", "")
        return display.replace("\\", ""), body, catch_all
    display = _CONVERTER.sub("{}", route)
    regex_parts, last = [], 0
    catch_all = False
    for match in _CONVERTER.finditer(route):
        regex_parts.append(re.escape(route[last:match.start()]))
        if match.group(1) == "path":
            regex_parts.append(".+")
            catch_all = True
        else:
            regex_parts.append("[^/]+")
        last = match.end()
    regex_parts.append(re.escape(route[last:]))
    return display, "".join(regex_parts), catch_all


def _module_lists(tree: ast.Module) -> dict[str, ast.AST]:
    return {t.id: n.value for n in tree.body if isinstance(n, ast.Assign) for t in n.targets
            if isinstance(t, ast.Name) and isinstance(n.value, (ast.List, ast.Tuple, ast.BinOp))}


class _UrlWalker:
    def __init__(self, view: ProjectView) -> None:
        self.view = view
        self.routes: list[Route] = []
        self.seen: set[tuple[str, str]] = set()

    def add(self, display: str, regex: str, methods: frozenset[str] | None, source: str, view_name: str = "",
            view_source: str = "", catch_all: bool = False, prefix_only: bool = False) -> None:
        path = "/" + display.lstrip("/")
        path = re.sub(r"/{2,}", "/", path)
        pattern = "/" + regex.lstrip("/").rstrip("/") if regex else "/"
        pattern = re.sub(r"(?:\\/|/){2,}", "/", pattern)
        self.routes.append(Route(path, methods, source, view_name, view_source, catch_all, prefix_only,
                                 pattern if pattern != "/" else "/?"))

    def routers(self, path: str, tree: ast.Module) -> dict[str, list[tuple[str, str, frozenset[str] | None, str, str]]]:
        """router variable -> [(display piece, regex piece, methods, view name, view source)]."""
        found: dict[str, list] = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) \
                    and _call_name(node.value) in _ROUTER_CLASSES:
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        found[target.id] = []
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "register" and isinstance(node.func.value, ast.Name)
                    and node.func.value.id in found and len(node.args) >= 2):
                continue
            prefix = _const(node.args[0])
            if prefix is None:
                continue
            viewset = ast.unparse(node.args[1])
            info = _class_info(self.view, viewset, path)
            prefix = prefix.strip("^$").strip("/")
            entries = found[node.func.value.id]
            collection = {v for a, v in (("list", "GET"), ("create", "POST")) if a in info.actions}
            detail = {v for a, v in (("retrieve", "GET"), ("update", "PUT"), ("partial_update", "PATCH"),
                                     ("destroy", "DELETE")) if a in info.actions}
            source = info.source
            esc = re.escape(prefix)
            if collection or not info.known:
                entries.append((f"{prefix}/", f"{esc}/", frozenset(collection) if info.known else None, viewset, source))
            if detail or not info.known:
                entries.append((f"{prefix}/{{}}/", f"{esc}/[^/]+/", frozenset(detail) if info.known else None,
                                viewset, source))
            for is_detail, url_path, verbs in info.extra:
                piece = f"{prefix}/{{}}/{url_path}/" if is_detail else f"{prefix}/{url_path}/"
                regex = f"{esc}/[^/]+/{re.escape(url_path)}/" if is_detail else f"{esc}/{re.escape(url_path)}/"
                entries.append((piece, regex, verbs, f"{viewset}.{url_path}", source))
        return found

    def walk(self, path: str, display_prefix: str, regex_prefix: str, depth: int = 0) -> None:
        if depth > 8 or (path, display_prefix) in self.seen:
            return
        self.seen.add((path, display_prefix))
        tree = self.view.tree(path)
        if tree is None:
            return
        routers = self.routers(path, tree)
        lists = _module_lists(tree)
        for stmt in tree.body:
            value = None
            if isinstance(stmt, ast.Assign) and any(getattr(t, "id", "") == "urlpatterns" for t in stmt.targets):
                value = stmt.value
            elif isinstance(stmt, ast.AugAssign) and getattr(stmt.target, "id", "") == "urlpatterns":
                value = stmt.value
            elif isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call) \
                    and isinstance(stmt.value.func, ast.Attribute) and stmt.value.func.attr in ("append", "extend") \
                    and getattr(stmt.value.func.value, "id", "") == "urlpatterns" and stmt.value.args:
                value = stmt.value.args[0]
                if stmt.value.func.attr == "append":
                    value = ast.List(elts=[value])
            if value is not None:
                self.entries(path, value, display_prefix, regex_prefix, routers, lists, depth)

    def entries(self, path: str, value: ast.AST, dprefix: str, rprefix: str, routers: dict, lists: dict,
                depth: int) -> None:
        if isinstance(value, (ast.List, ast.Tuple)):
            for element in value.elts:
                self.entries(path, element, dprefix, rprefix, routers, lists, depth)
        elif isinstance(value, ast.BinOp):
            self.entries(path, value.left, dprefix, rprefix, routers, lists, depth)
            self.entries(path, value.right, dprefix, rprefix, routers, lists, depth)
        elif isinstance(value, ast.Name) and value.id in lists:
            self.entries(path, lists[value.id], dprefix, rprefix, routers, {}, depth)
        elif isinstance(value, ast.Attribute) and value.attr == "urls" and getattr(value.value, "id", "") in routers:
            for piece, regex, methods, view_name, source in routers[value.value.id]:
                self.add(dprefix + piece, rprefix + regex, methods, path, view_name, source)
        elif isinstance(value, ast.Call) and _call_name(value) in ("path", "re_path", "url"):
            self.route(path, value, dprefix, rprefix, routers, lists, depth)

    def route(self, path: str, call: ast.Call, dprefix: str, rprefix: str, routers: dict, lists: dict,
              depth: int) -> None:
        args = list(call.args)
        keywords = {k.arg: k.value for k in call.keywords}
        route = _const(args[0] if args else keywords.get("route"))
        target = args[1] if len(args) > 1 else keywords.get("view")
        if route is None or target is None:
            return
        piece, regex, catch_all = _path_regex(route, _call_name(call) in ("re_path", "url"))
        display, pattern = dprefix + piece, rprefix + regex
        if isinstance(target, ast.Call) and _call_name(target) == "include" and target.args:
            included = target.args[0]
            if isinstance(included, (ast.Tuple, ast.List)) and included.elts:
                included = included.elts[0]
            module = _const(included)
            if module is not None:
                file = resolve_module(self.view, module, path)
                if file:
                    self.walk(file, display, pattern, depth + 1)
                else:
                    self.add(display, pattern, None, path, module, prefix_only=True)
            elif isinstance(included, ast.Attribute) and included.attr == "urls" \
                    and getattr(included.value, "id", "") in routers:
                for sub, sub_regex, methods, view_name, source in routers[included.value.id]:
                    self.add(display + sub, pattern + sub_regex, methods, path, view_name, source)
            elif isinstance(included, (ast.List, ast.Name)):
                self.entries(path, included, display, pattern, routers, lists, depth)
            else:
                self.add(display, pattern, None, path, ast.unparse(included), prefix_only=True)
            return
        if isinstance(target, ast.Attribute) and target.attr == "urls":  # admin.site.urls
            self.add(display, pattern, None, path, ast.unparse(target), prefix_only=True)
            return
        methods: frozenset[str] | None = None
        view_name, view_source = ast.unparse(target), ""
        if isinstance(target, ast.Call) and isinstance(target.func, ast.Attribute) and target.func.attr == "as_view":
            class_name = ast.unparse(target.func.value)
            view_name = class_name
            mapping = target.args[0] if target.args and isinstance(target.args[0], ast.Dict) else None
            info = _class_info(self.view, class_name, path)
            view_source = info.source
            if mapping is not None:
                methods = frozenset((_const(k) or "").upper() for k in mapping.keys if _const(k))
            elif info.known:
                methods = frozenset(info.methods | ({"GET"} if not info.methods else set())) \
                    if info.methods or class_name.rsplit(".", 1)[-1] == "TemplateView" else None
        elif isinstance(target, (ast.Name, ast.Attribute)):
            methods = _function_methods(self.view, view_name, path)
        self.add(display, pattern, methods, path, view_name, view_source, catch_all=catch_all)


def settings_modules(view: ProjectView) -> list[str]:
    found = []
    for path in sorted(view.files):
        if not path.endswith(".py"):
            continue
        text = view.read(path) or ""
        if "ROOT_URLCONF" in text and "INSTALLED_APPS" in text:
            found.append(path)
    return found


def django_routes(view: ProjectView) -> list[Route]:
    walker = _UrlWalker(view)
    for settings in settings_modules(view):
        tree = view.tree(settings)
        if tree is None:
            continue
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "ROOT_URLCONF" for t in node.targets):
                module = _const(node.value)
                target = resolve_module(view, module, settings) if module else None
                if target:
                    walker.walk(target, "", "")
    unique: dict[tuple[str, str], Route] = {}
    for route in walker.routes:
        unique.setdefault((route.path, route.view), route)
    return list(unique.values())


# ------------------------------------------------------------------ frontend API calls
@dataclass
class ApiCall:
    method: str
    path: str        # "/api/orders/{}/status/"; {} is a value only known at run time
    file: str
    line: int
    url: str         # as written


_CALL = re.compile(r"(?P<recv>[A-Za-z_$][\w$]*(?:\s*\.\s*[A-Za-z_$][\w$]*)*)\s*\.\s*(?P<method>get|post|put|patch|delete)"
                   r"\s*(?:<[^<>()]*>)?\s*\(\s*(?=[`'\"])")
_FETCH = re.compile(r"\bfetch\s*\(\s*(?=[`'\"])")
_CONST_DEF = re.compile(r"(?m)^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*(?::[^=]+)?=\s*(.+)$")
_CREATE = re.compile(r"(?:(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*)?axios\s*\.\s*create\s*\(\s*\{(?P<body>.*?)\}\s*\)",
                     re.S)
_BASE_URL = re.compile(r"baseURL\s*:\s*([^,\n}]+)")
_LOCAL_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0", "[::1]")


def _read_literal(text: str, start: int) -> tuple[str, str, int] | None:
    """(quote, content, end index) of the string literal starting at ``start``."""
    quote = text[start]
    i, depth = start + 1, 0
    while i < len(text):
        char = text[i]
        if char == "\\":
            i += 2
            continue
        if quote == "`" and text.startswith("${", i):
            depth += 1
            i += 2
            continue
        if quote == "`" and depth and char == "}":
            depth -= 1
        elif char == quote and not depth:
            return quote, text[start + 1:i], i + 1
        elif char == "\n" and quote != "`":
            return None
        i += 1
    return None


def _url_path(url: str) -> str | None:
    """The path of a URL on this app's own server; None for other hosts."""
    found = re.match(r"https?://([^/]+)(/.*)?$", url)
    if found:
        host = found.group(1).split(":")[0] if not found.group(1).startswith("[") else found.group(1)
        if host not in _LOCAL_HOSTS:
            return None
        return found.group(2) or "/"
    return url


def _literals(expr: str) -> list[str]:
    return [m.group(2) for m in re.finditer(r"([`'\"])((?:(?!\1).)*)\1", expr)]


def _resolve_base(expr: str, consts: dict[str, str], depth: int = 0) -> str | None:
    """The path a base-URL expression points at: its last literal URL/path (the fallback after ``||``)."""
    expr = expr.strip().rstrip(";").strip()
    for literal in reversed(_literals(expr)):
        if literal.startswith(("http://", "https://", "/")):
            built = _template_path(literal, consts, depth + 1) if "${" in literal else _url_path(literal)
            if built is not None:
                return built
    if re.fullmatch(r"[A-Za-z_$][\w$]*", expr) and expr in consts:
        return consts[expr]
    return None


def _template_path(content: str, consts: dict[str, str], depth: int = 0) -> str | None:
    """A template/plain URL as a path with {} for values known only at run time; None when the start is unknown."""
    parts = re.split(r"(\$\{[^}]*\})", content)
    out = ""
    for index, part in enumerate(parts):
        if part.startswith("${"):
            name = part[2:-1].strip()
            if not out and index <= 1:  # the URL starts with a variable: it must be a known base
                base = consts.get(name) if depth < 4 else None
                if base is None:
                    return None
                out = base
            else:
                out += "{}"
        else:
            out += part
    path = _url_path(out)
    if path is None:
        return None
    return path.split("?")[0].split("#")[0]


def _consts(text: str) -> dict[str, str]:
    consts: dict[str, str] = {}
    for match in _CONST_DEF.finditer(text):
        base = _resolve_base(match.group(2), consts)
        if base is not None:
            consts[match.group(1)] = base
    return consts


def _join_url(base: str, url: str) -> str:
    if not base:
        return url
    return base.rstrip("/") + "/" + url.lstrip("/")


def _instance_bases(text: str, consts: dict[str, str]) -> tuple[dict[str, str], str | None]:
    """(instance variable -> base path, the file's first axios.create base)."""
    named: dict[str, str] = {}
    first: str | None = None
    for match in _CREATE.finditer(text):
        found = _BASE_URL.search(match.group("body"))
        base = _resolve_base(found.group(1), consts) if found else ""
        if base is None:
            continue
        if match.group(1):
            named[match.group(1)] = base
        if first is None:
            first = base
    return named, first


def frontend_calls(view: ProjectView) -> list[ApiCall]:
    js_files = sorted(f for f in view.files if jsinterface.is_js(f) and "node_modules/" not in f
                      and not PurePosixPath(f).name.startswith(("vite.config", "vitest.config", "postcss.config",
                                                                 "tailwind.config", "eslint")))
    module_base: dict[str, str] = {}
    texts: dict[str, str] = {}
    consts_by_file: dict[str, dict[str, str]] = {}
    for path in js_files:
        text = jsinterface._strip_comments(view.read(path) or "")
        texts[path] = text
        consts_by_file[path] = _consts(text)
        _, first = _instance_bases(text, consts_by_file[path])
        if first is not None:
            module_base[path] = first
    calls: list[ApiCall] = []
    for path in js_files:
        text, consts = texts[path], dict(consts_by_file[path])
        named, own_base = _instance_bases(text, consts)
        imported: dict[str, str] = {}
        uses_axios = bool(re.search(r"""from\s+['"]axios['"]|require\(\s*['"]axios['"]\s*\)""", text))
        for match in jsinterface._IMPORT.finditer(text):
            target = jsinterface.resolve(path, match.group("src"), view.exists) \
                if match.group("src").startswith(".") else None
            if target is None:
                continue
            default, names, namespace = jsinterface._parse_clause(match.group("clause"))
            for local in [n for n in (default, namespace, *names) if n]:
                if target in module_base:
                    imported[local] = module_base[target]
                if local in consts_by_file.get(target, {}):
                    consts.setdefault(local, consts_by_file[target][local])
        for match in _CALL.finditer(text):
            receiver = re.sub(r"\s+", "", match.group("recv"))
            literal = _read_literal(text, match.end())
            if literal is None:
                continue
            root = receiver.split(".")[0]
            if receiver == "axios":
                base: str | None = ""
            elif receiver in named:
                base = named[receiver]
            elif root in imported:
                base = imported[root]
            elif own_base is not None and (uses_axios or receiver not in ("map", "params", "searchParams")):
                base = own_base
            elif uses_axios or imported:
                base = ""
            else:
                continue  # Map.get('x'), URLSearchParams.get('id') ...: not an HTTP call
            calls += _make_call(match.group("method").upper(), literal, base, consts, path, text, match.start())
        for match in _FETCH.finditer(text):
            literal = _read_literal(text, match.end())
            if literal is None:
                continue
            window = text[literal[2]:literal[2] + 400].split("fetch(")[0]
            verb = re.search(r"method\s*:\s*['\"`](\w+)", window)
            calls += _make_call(verb.group(1).upper() if verb else "GET", literal, "", consts, path, text,
                                match.start())
    unique: dict[tuple[str, str, str], ApiCall] = {}
    for call in calls:
        unique.setdefault((call.method, call.path, call.file), call)
    return list(unique.values())


def _make_call(method: str, literal: tuple[str, str, int], base: str | None, consts: dict[str, str], path: str,
               text: str, offset: int) -> list[ApiCall]:
    quote, content, _ = literal
    if quote == "`" and "${" in content:
        url = _template_path(content, consts)
        if url is None:
            return []
        if base and not content.startswith(("${", "http://", "https://")):
            url = _join_url(base, url)  # axios joins baseURL and the URL, leading slash or not
    else:
        url = _url_path(content)
        if url is None:
            return []
        if base and not content.startswith(("http://", "https://")):
            url = _join_url(base, url)
    if not url.startswith("/"):
        return []
    url = url.split("?")[0].split("#")[0]
    url = re.sub(r"/{2,}", "/", url)
    segments = [s for s in url.split("/") if s]
    if segments and all(s == "{}" for s in segments[-1:]) and len([s for s in segments if s != "{}"]) == 0:
        return []  # nothing but run-time values: cannot be checked
    return [ApiCall(method, url, path, text.count("\n", 0, offset) + 1, content)]


# ------------------------------------------------------------------ environment variables
_ENV_PATTERNS = (
    re.compile(r"""os\.environ\.get\(\s*['"]([A-Za-z_][A-Za-z0-9_]*)"""),
    re.compile(r"""os\.getenv\(\s*['"]([A-Za-z_][A-Za-z0-9_]*)"""),
    re.compile(r"""os\.environ\[\s*['"]([A-Za-z_][A-Za-z0-9_]*)"""),
    re.compile(r"""\b(?:env|config)(?:\.\w+)?\(\s*['"]([A-Z_][A-Z0-9_]*)['"]"""),
    re.compile(r"""import\.meta\.env\.([A-Za-z_][A-Za-z0-9_]*)"""),
    re.compile(r"""process\.env\.([A-Za-z_][A-Za-z0-9_]*)"""),
)


def env_vars(view: ProjectView) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for path in sorted(view.files):
        if not (path.endswith(".py") or jsinterface.is_js(path)):
            continue
        text = view.read(path) or ""
        for pattern in _ENV_PATTERNS:
            for name in pattern.findall(text):
                if name in ("MODE", "DEV", "PROD", "SSR", "BASE_URL", "NODE_ENV") and "import.meta" in text:
                    continue
                files = found.setdefault(name, [])
                if path not in files:
                    files.append(path)
    return found


# ------------------------------------------------------------------ layout
def layout(view: ProjectView) -> list[str]:
    lines = []
    for manage in view.named("manage.py"):
        lines.append(f"Django project: {manage} (run manage.py from {str(PurePosixPath(manage).parent)}/)")
    for manifest in view.named("requirements.txt", "pyproject.toml"):
        lines.append(f"Python dependencies: {manifest}")
    for package in view.named("package.json"):
        folder = str(PurePosixPath(package).parent)
        data = package_json(view, folder) or {}
        scripts = ", ".join(sorted((data.get("scripts") or {}))) if isinstance(data.get("scripts"), dict) else ""
        lines.append(f"Node package: {package}" + (f" (scripts: {scripts})" if scripts else ""))
    for config in view.matching("vite.config.*"):
        folder = str(PurePosixPath(config).parent)
        lines.append(f"Vite app: {config} (builds to {join(folder, build_output_dir(view, folder) or 'dist')}/)")
    for docker in view.matching("Dockerfile", "Dockerfile.*", "*.dockerfile"):
        lines.append(f"Container: {docker}")
    for compose in view.matching("docker-compose*.yml", "docker-compose*.yaml", "compose.yml", "compose.yaml"):
        lines.append(f"Compose file: {compose}")
    return lines
