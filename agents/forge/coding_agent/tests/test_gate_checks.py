"""Fixes from the burger-shop build: JavaScript imports, Django-style references, removed code,
work left to other divisions, and a test run with the project's dependencies."""

import subprocess
import sys
from pathlib import Path, PurePosixPath

import pytest
from conftest import ScriptedLLM

from coding_agent import CodingAgent, CodingRequest, InvocationContext, InvocationSource
from coding_agent.core import jsinterface, testrun
from coding_agent.core.models import FileChange
from coding_agent.core.pyinterface import dotted_reference_problems, import_problems
from coding_agent.core.verification import verify_files
from coding_agent.core.workspace import ChangeSet, Workspace, WorkspaceError, is_secret

UI = InvocationContext(source=InvocationSource.UI)


def check_js(files, changed=None):
    return jsinterface.import_problems(sorted(files), files.get, files.__contains__, changed)


# ---------------------------------------------------------------- 1. javascript imports
API = "export const fetchCategories = async () => [];\nexport function formatPrice(p) { return p; }\n"
CONTEXT = "export const CartProvider = ({ children }) => children;\nexport const useCart = () => ({});\n"


def test_js_imports_that_cannot_work_are_reported():
    files = {
        "src/services/api.js": API,
        "src/context/CartContext.jsx": CONTEXT,
        "src/App.jsx": ("import React from 'react';\nimport Header from './components/Header';\n"
                        "import CartProvider from './context/CartContext';\nimport './index.css';\n"),
        "src/pages/Menu.jsx": ("import api from '../services/api';\n"
                               "import { fetchCategories, getItems } from '../services/api';\n"),
        "src/pages/Board.jsx": "import * as api from '../services/api';\napi.fetchCategories();\napi.placeOrder();\n",
        "src/index.css": "body {}\n",
    }
    details = [p["path"] + ": " + p["detail"] for p in check_js(files)]
    assert any("App.jsx" in d and "'./components/Header', but no such file" in d for d in details)
    assert any("App.jsx" in d and "default-imports 'CartProvider'" in d and "no default export" in d for d in details)
    assert any("Menu.jsx" in d and "default-imports 'api'" in d for d in details)
    assert any("Menu.jsx" in d and "'getItems'" in d and "fetchCategories, formatPrice" in d for d in details)
    assert any("Board.jsx" in d and "api.placeOrder" in d for d in details)
    assert not any("fetchCategories from" in d or "index.css" in d or "react" in d for d in details)
    assert len(details) == 5


def test_valid_js_imports_pass():
    files = {
        "src/api/index.js": "export default { get: () => 1 };\nexport { a as b } from './a';\n",
        "src/api/a.ts": "export const a = 1;\nexport type Thing = string;\n",
        "src/App.tsx": ("import api, { b } from './api';\nimport {\n  a,\n  type Thing,\n} from './api/a';\n"
                        "// import Missing from './missing';\n/* import Gone from './gone'; */\n"),
        "src/star.js": "export * from './api/a';\n",
        "src/useStar.js": "import { anything } from './star';\n",
    }
    assert check_js(files) == []


def test_js_problems_are_limited_to_what_this_run_changed():
    files = {"src/old.js": "import x from './gone';\n", "src/new.js": "import y from './nothere';\n"}
    assert [p["path"] for p in check_js(files, changed={"src/new.js"})] == ["src/new.js"]


# ---------------------------------------------------------------- 2. python references and removed code
DJANGO = {
    "backend/manage.py": "import os\n",
    "backend/menu/__init__.py": "",
    "backend/menu/models.py": "class MenuItem:\n    pass\n",
    "backend/menu/apps.py": "class MenuConfig:\n    pass\n",
    "backend/orders/__init__.py": "",
    "backend/orders/views.py": ("from menu.models import MenuItem, AddOn\nfrom .serializers import OrderSerializer\n"
                                "import django.db\nimport requests\n"),
    "backend/shop/__init__.py": "",
    "backend/shop/urls.py": ("from django.urls import include, path\n"
                             "urlpatterns = [path('api/', include('menu.urls')), path('o/', include('orders.views'))]\n"),
    "backend/shop/settings.py": (
        "ROOT_URLCONF = 'shop.urls'\nWSGI_APPLICATION = 'shop.wsgi.application'\n"
        "INSTALLED_APPS = ['django.contrib.admin', 'menu.apps.MenuConfig', 'orders', 'billing',\n"
        "                  'whitenoise.middleware.WhiteNoiseMiddleware']\n"
        "MIDDLEWARE = ['django.middleware.common.CommonMiddleware', 'shop.middleware.Timing']\n"),
}


def test_imports_resolve_from_the_folder_a_django_project_runs_in():
    exists = DJANGO.__contains__
    details = [p["detail"] for p in import_problems(sorted(DJANGO), DJANGO.get, exists)]
    assert any("'AddOn' from backend/menu/models.py" in d for d in details)
    assert any("from .serializers, but backend/orders/serializers.py does not exist" in d for d in details)
    assert not any("django" in d or "requests" in d for d in details)


def test_dotted_references_in_django_files_are_checked():
    details = [p["detail"] for p in dotted_reference_problems(sorted(DJANGO), DJANGO.get, DJANGO.__contains__)]
    assert any("include() names 'menu.urls', but backend/menu/urls.py does not exist" in d for d in details)
    assert any("WSGI_APPLICATION 'shop.wsgi.application', but backend/shop/wsgi.py" in d for d in details)
    assert any("WhiteNoiseMiddleware', which is a middleware class" in d for d in details)
    assert any("MIDDLEWARE lists 'shop.middleware.Timing'" in d for d in details)
    joined = "\n".join(details)
    for fine in ("orders.views", "shop.urls", "menu.apps.MenuConfig", "'orders'", "billing", "django.contrib"):
        assert fine not in joined, fine


VIEWS_V1 = "def order_create(request):\n    pass\n\n\ndef order_detail(request, pk):\n    pass\n"
URLS_V1 = ("from django.urls import path\nfrom . import views\n"
           "urlpatterns = [path('orders/', views.order_create), path('orders/<int:pk>/', views.order_detail)]\n")
VIEWS_V2 = "def kitchen_orders(request):\n    pass\n"
URLS_V2 = "from django.urls import path\nfrom . import views\nurlpatterns = [path('kitchen/', views.kitchen_orders)]\n"


def kitchen_workspace(tmp_path):
    ws = tmp_path / "ws"
    (ws / "orders").mkdir(parents=True)
    (ws / "orders" / "__init__.py").write_text("")
    (ws / "orders" / "views.py").write_text(VIEWS_V1)
    (ws / "orders" / "urls.py").write_text(URLS_V1)
    return ws


def test_a_change_that_drops_existing_code_is_flagged(tmp_path):
    changes = ChangeSet(Workspace(kitchen_workspace(tmp_path)))
    changes.add(FileChange("orders/views.py", "modify", VIEWS_V2))
    changes.add(FileChange("orders/urls.py", "modify", URLS_V2))
    problems = {p["path"]: p["detail"] for p in verify_files(changes, []).problems if p["check"] == "removed-code"}
    assert "order_create, order_detail" in problems["orders/views.py"]
    assert "route 'orders/'" in problems["orders/urls.py"] and "route 'orders/<int:pk>/'" in problems["orders/urls.py"]

    changes.add(FileChange("orders/views.py", "modify", VIEWS_V1 + "\n\n" + VIEWS_V2))
    changes.add(FileChange("orders/urls.py", "modify", URLS_V1))
    assert not [p for p in verify_files(changes, []).problems if p["check"] == "removed-code"]


def test_dropped_code_goes_to_repair_with_the_old_version_then_warns(tmp_path):
    ws = kitchen_workspace(tmp_path)
    llm = ScriptedLLM({"python_developer": {"summary": "kitchen", "files": [
        {"path": "orders/views.py", "action": "modify", "content": VIEWS_V2}]},
        "debugging_specialist": {"summary": "kept it as is", "files": []}})
    report = CodingAgent.with_client(llm, max_repairs=1).unit("python_developer").run(
        CodingRequest(task="Add a kitchen view", workspace=str(ws)), UI)
    repair = next(c["prompt"] for c in llm.calls if c["role"] == "debugging_specialist")
    assert "[removed-code]" in repair and "orders/views.py before this run" in repair and "def order_create" in repair
    assert report.status == "completed", report.error
    assert any("removes order_create, order_detail" in w for w in report.warnings)


def test_builders_are_told_to_keep_existing_code():
    from coding_agent.core.prompts import _KIND_INSTRUCTIONS
    assert "keep every function, class, export and URL route" in _KIND_INSTRUCTIONS["builder"]


def test_removed_js_exports_are_flagged(tmp_path):
    (tmp_path / "api.js").write_text(API)
    changes = ChangeSet(Workspace(tmp_path))
    changes.add(FileChange("api.js", "modify", "export const fetchCategories = async () => [];\n"))
    problems = [p for p in verify_files(changes, []).problems if p["check"] == "removed-code"]
    assert problems and "export formatPrice" in problems[0]["detail"]


# ---------------------------------------------------------------- 3. routing and secrets templates
@pytest.mark.parametrize("name, secret", [
    (".env.example", False), (".env.sample", False), (".env.template", False),
    (".env", True), (".env.local", True), (".env.production", True), ("server.key", True)])
def test_env_templates_are_not_secrets(name, secret):
    assert is_secret(PurePosixPath(name)) is secret


def test_env_example_can_be_written(tmp_path):
    changes = ChangeSet(Workspace(tmp_path))
    changes.add(FileChange(".env.example", "create", "SECRET_KEY=change-me\n"))
    with pytest.raises(WorkspaceError):
        changes.add(FileChange(".env.local", "create", "SECRET_KEY=x\n"))


def test_leads_are_told_what_other_divisions_build(tmp_path):
    llm = ScriptedLLM({
        "engineering_director": {"rationale": "x", "team": ["tech_lead", "python_developer", "frontend_developer",
                                                            "devops_engineer", "docker_engineer"]},
    })
    CodingAgent.with_client(llm).run(
        CodingRequest(task="Containerize the Python app with Docker", workspace=str(tmp_path)), UI)
    tech_lead = next(c["prompt"] for c in llm.calls if c["role"] == "tech_lead")
    assert "## Other divisions building on this run" in tech_lead
    assert "Docker Engineer (DevOps and Infrastructure division)" in tech_lead and "Dockerfiles" in tech_lead
    assert "Python Developer (" not in tech_lead.split("## Other divisions building on this run")[1]


# ---------------------------------------------------------------- 4. test run with dependencies
def test_only_plain_requirements_are_installed():
    accepted, refused = testrun.parse_requirements(
        "Django>=5.0\npsycopg[binary]>=3.0,<4\nwhitenoise  # static files\n"
        "pywin32; sys_platform == 'win32'\n# comment\n\n"
        "-e .\n-r other.txt\n--index-url https://evil.example/simple\n"
        "git+https://github.com/x/y.git\nhttps://example.com/pkg.whl\n./local_pkg\n")
    assert accepted == ["Django>=5.0", "psycopg[binary]>=3.0,<4", "whitenoise", "pywin32; sys_platform == 'win32'"]
    assert len(refused) == 6


def test_dependency_environments_are_cached_and_only_use_wheels(tmp_path, monkeypatch):
    monkeypatch.setenv("CODING_AGENT_TEST_DEPS", "install")
    monkeypatch.setattr(testrun, "_PREPARED", {})
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        if argv[1:3] == ["-m", "venv"]:
            python = testrun._venv_python(Path(argv[3]))
            python.parent.mkdir(parents=True, exist_ok=True)
            python.write_text("")
        assert "GEMINI_API_KEY" not in (kwargs.get("env") or {})
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(testrun.subprocess, "run", fake_run)
    monkeypatch.setenv("GEMINI_API_KEY", "secret")
    ws = tmp_path / "ws"
    (ws / "backend").mkdir(parents=True)
    (ws / "backend" / "requirements.txt").write_text("Django>=5.0\n-e .\n")
    changes = ChangeSet(Workspace(ws))
    env = testrun.prepare_python(changes)
    assert env.has_dependencies and env.packages == ("Django>=5.0",) and "refused 1 line" in env.note
    pip = calls[-1]
    assert "--only-binary=:all:" in pip and pip[-1] == "Django>=5.0" and "-e" not in pip
    assert str(tmp_path / "testrun-envs") in env.python

    monkeypatch.setattr(testrun, "_PREPARED", {})
    calls.clear()
    assert "cached" in testrun.prepare_python(changes).note and calls == []


def test_deps_off_or_no_requirements_uses_the_agents_python(tmp_path, monkeypatch):
    (tmp_path / "requirements.txt").write_text("Django\n")
    changes = ChangeSet(Workspace(tmp_path))
    assert testrun.prepare_python(changes).python == sys.executable  # conftest sets CODING_AGENT_TEST_DEPS=off
    monkeypatch.setenv("CODING_AGENT_TEST_DEPS", "install")
    assert testrun.prepare_python(ChangeSet(Workspace(tmp_path / "empty"))).python == sys.executable


def run_script(tmp_path, files, command, env=None):
    changes = ChangeSet(Workspace(tmp_path / "ws"))
    for path, content in files.items():
        changes.add(FileChange(path, "create", content))
    results = testrun.run_commands(changes, [{"command": command}], 10, env)
    return results[0], changes


def test_a_script_in_a_subfolder_runs_from_its_own_folder(tmp_path):
    result, _ = run_script(tmp_path, {"backend/manage.py": "import os\nprint(os.path.basename(os.getcwd()))\n"},
                           "python manage.py check")
    assert result.ran_as == "cd backend && python manage.py check" and result.stdout.strip() == "backend"


def test_a_missing_database_is_not_a_crash(tmp_path):
    result, changes = run_script(tmp_path, {"app.py": "raise ConnectionRefusedError(111, 'Connection refused')\n"},
                                 "python app.py")
    assert result.crashed and not result.failed and "needs a service" in result.unavailable
    assert testrun.problems_from([result], changes, 10) == []


def test_a_missing_package_is_unavailable_without_deps_and_a_bug_with_them(tmp_path):
    script = {"app.py": "import notapackage_xyz\n", "requirements.txt": "requests\n"}
    result, _ = run_script(tmp_path / "a", script, "python app.py")
    assert not result.failed and "notapackage_xyz" in result.unavailable

    with_deps = testrun.PythonEnv(sys.executable, packages=("requests",))
    result, changes = run_script(tmp_path / "b", script, "python app.py", with_deps)
    problems = testrun.problems_from([result], changes, 10)
    assert result.failed and problems[0]["path"] == "requirements.txt"
    assert "'notapackage_xyz' is imported but not listed in requirements.txt" in problems[0]["detail"]


def test_import_errors_printed_without_a_traceback_still_fail(tmp_path):
    script = {"manage.py": ("import sys\ntry:\n    import notapackage_xyz\nexcept ImportError as exc:\n"
                            "    print('Could not start.', exc, file=sys.stderr)\n    sys.exit(1)\n")}
    result, _ = run_script(tmp_path, script, "python manage.py", testrun.PythonEnv(sys.executable, packages=("x",)))
    assert result.failed and "could not import module 'notapackage_xyz'" in result.reported


def test_a_long_traceback_is_still_a_crash(tmp_path):
    result, _ = run_script(tmp_path, {"app.py": "raise ValueError('x' * 5000)\n"}, "python app.py")
    assert result.crashed and result.failed and "[earlier frames cut]" in result.stderr


def test_django_check_errors_point_at_the_file_django_names(tmp_path):
    script = {"backend/manage.py": (
        "import sys\nsys.stderr.write(\"SystemCheckError: System check identified some issues:\\n\\nERRORS:\\n"
        "<class 'menu.admin.ItemAdmin'>: (admin.E027) bad field\\n\")\nsys.exit(1)\n"),
        "backend/menu/__init__.py": "", "backend/menu/admin.py": "x = 1\n"}
    result, changes = run_script(tmp_path, script, "python backend/manage.py check")
    problems = testrun.problems_from([result], changes, 10)
    assert result.reported == "Django system check reported errors"
    assert problems[0]["path"] == "backend/menu/admin.py" and "admin.E027" in problems[0]["detail"]


def test_the_plan_prompt_explains_subfolders_and_services():
    from coding_agent.core.prompts import TEST_PLAN_INSTRUCTIONS
    assert "python backend/manage.py check" in TEST_PLAN_INSTRUCTIONS
    assert "no database server" in TEST_PLAN_INSTRUCTIONS
