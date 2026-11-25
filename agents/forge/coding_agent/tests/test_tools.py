"""The team's tools: facts read from the code, project-wide checks, fixes made without a model, the
builder self-check, the project brief and changelog, and how leads' plans are handled."""

import json

from conftest import ScriptedLLM

from coding_agent import CodingAgent, CodingRequest, InvocationContext, InvocationSource
from coding_agent.core import autofix, checks, projectdocs, testrun
from coding_agent.core.llm import LLMError
from coding_agent.core.models import FileChange, RunReport, VerificationResult
from coding_agent.core.projectfacts import ProjectView, django_routes, frontend_calls
from coding_agent.core.workspace import ChangeSet, Workspace

UI = InvocationContext(source=InvocationSource.UI)


def view_of(files: dict[str, str]) -> ProjectView:
    return ProjectView(files, files.get)


# ---------------------------------------------------------------- facts: routes and calls
BACKEND = {
    "backend/manage.py": "import sys\n",
    "backend/orders/__init__.py": "",
    "backend/shop/settings.py": "ROOT_URLCONF = 'shop.urls'\nINSTALLED_APPS = ['rest_framework', 'orders']\n",
    "backend/shop/urls.py": (
        "from django.contrib import admin\nfrom django.urls import include, path, re_path\n"
        "from django.views.generic import TemplateView\n\n"
        "urlpatterns = [\n    path('admin/', admin.site.urls),\n    path('api/', include('orders.urls')),\n"
        "    re_path(r'^(?!api/).*$', TemplateView.as_view(template_name='index.html')),\n]\n"),
    "backend/orders/urls.py": (
        "from rest_framework.routers import DefaultRouter\nfrom .views import OrderViewSet\n\n"
        "router = DefaultRouter()\nrouter.register(r'orders', OrderViewSet, basename='order')\n"
        "urlpatterns = router.urls\n"),
    "backend/orders/views.py": (
        "from rest_framework import mixins, viewsets\nfrom rest_framework.decorators import action\n"
        "from rest_framework.response import Response\n\n\n"
        "class OrderViewSet(mixins.CreateModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):\n"
        "    def update(self, request, pk=None):\n        return Response({})\n\n"
        "    @action(detail=True, methods=['get'], url_path='status')\n"
        "    def order_status(self, request, pk=None):\n        return Response({})\n"),
}
API_JS = (
    "import axios from 'axios';\n\n"
    "/* the API lives under /api/\n   on the same server */\n"
    "const API = import.meta.env.VITE_API_URL || '/api/';\n"
    "const client = axios.create({ baseURL: API });\n\n"
    "export const placeOrder = (order) => client.post('orders/', order);\n"
    "export const orderStatus = (id) => client.get(`orders/${id}/status/`);\n"
    "export const listOrders = () => client.get('/orders/');\n"
    "export const setStatus = (id, s) => client.patch(`/orders/${id}/`, { status: s });\n"
    "export const menu = () => fetch('/api/menu/');\n"
)


def test_django_routes_come_with_the_methods_their_views_allow():
    routes = {r.path: r for r in django_routes(view_of(BACKEND))}
    assert routes["/admin/"].prefix_only
    assert routes["/api/orders/"].methods == {"POST"}                 # create only: no list
    assert routes["/api/orders/{}/"].methods == {"GET", "PUT"}         # retrieve + update, no partial_update
    assert routes["/api/orders/{}/status/"].methods == {"GET"}         # @action
    assert any(r.catch_all for r in routes.values())                   # the SPA fallback


def test_frontend_calls_resolve_base_urls_and_templates():
    calls = {(c.method, c.path): c for c in frontend_calls(view_of({"frontend/src/api.js": API_JS}))}
    assert set(calls) == {("POST", "/api/orders/"), ("GET", "/api/orders/{}/status/"), ("GET", "/api/orders/"),
                          ("PATCH", "/api/orders/{}/"), ("GET", "/api/menu/")}
    assert calls[("GET", "/api/menu/")].line == 12   # line numbers survive the block comment


def test_api_calls_are_checked_against_the_backend():
    problems = checks.api_contract_problems(view_of({**BACKEND, "frontend/src/api.js": API_JS}))
    details = [p["detail"] for p in problems]
    assert len(problems) == 3, details
    assert any("GET /api/orders/, but the backend route /api/orders/ (OrderViewSet) only allows POST" in d
               for d in details)
    assert any("PATCH /api/orders/{}/" in d and "only allows GET, PUT" in d for d in details)
    assert any("GET /api/menu/ matches no backend route" in d for d in details)   # the SPA catch-all does not count
    assert all(p["path"] == "frontend/src/api.js" for p in problems)


# ---------------------------------------------------------------- undefined names
def test_undefined_names_follow_python_scoping():
    source = (
        "from __future__ import annotations\nimport os\n\n\n"
        "def f(x: Missing) -> Other:\n    return helper(x) + os.sep\n\n\n"
        "class A:\n    y = 1\n\n    def m(self):\n        return y\n\n\n"
        "def outer():\n    z = 1\n\n    def inner():\n        return z\n    return inner\n\n\n"
        "def g():\n    global counter\n    counter = 1\n\n\n"
        "def h():\n    return counter\n"
    )
    assert [(n, line) for n, line, _ in checks.undefined_names(source)] == [("helper", 6), ("y", 13)]


def test_names_used_at_import_time_before_their_definition():
    source = (
        "from typing import List\nfrom rest_framework import serializers\nDEFAULT = 3\n\n\n"
        "def f(x=DEFAULT):\n    return Later()\n\n\n"
        "class Read(serializers.Serializer):\n    items: List[Item] = serializers.SerializerMethodField()\n"
        "    size = DEFAULT\n    double = size * 2\n\n    def m(self) -> 'Later':\n        return Item\n\n\n"
        "class Item(serializers.Serializer):\n    pass\n\n\nclass Later:\n    pass\n"
    )
    assert checks.used_before_definition(source) == [("Item", 11, 19)]
    deferred = "from __future__ import annotations\n" + source   # annotations are not evaluated any more
    assert checks.used_before_definition(deferred) == []


def test_imports_through_the_folder_the_code_runs_from_are_fixed():
    files = {"backend/manage.py": "", "backend/menu/__init__.py": "", "backend/menu/models.py": "class A:\n    pass\n",
             "backend/orders/services.py": "from backend.menu.models import A\nimport backend.menu.models as m\n"}
    problems = checks.root_import_problems(view_of(files), ["backend/orders/services.py"])
    assert [p["detail"] for p in problems][0] == ("line 1: imports backend.menu.models, but the code runs from "
                                                  "backend/ (where manage.py is), so that module is menu.models: "
                                                  "import it as menu.models")
    fixes = autofix.import_fixes(view_of(files), ["backend/orders/services.py"])
    assert fixes[0].content == "from menu.models import A\nimport menu.models as m\n"


def test_migrations_are_made_for_the_projects_own_apps(tmp_path):
    changes = ChangeSet(Workspace(tmp_path))
    for path, content in {
        "backend/manage.py": "",
        "backend/shop/settings.py": ("ROOT_URLCONF = 'shop.urls'\nINSTALLED_APPS = ['django.contrib.admin', "
                                     "'rest_framework', 'menu', 'orders.apps.OrdersConfig', 'pages']\n"),
        "backend/menu/models.py": "", "backend/orders/models.py": "", "backend/pages/views.py": "",
    }.items():
        changes.add(FileChange(path, "create", content))
    files = set(changes.paths())
    assert testrun.project_app_labels(changes, "backend", files) == ["menu", "orders"]   # pages has no models


def test_star_imports_turn_the_check_off():
    assert checks.undefined_names("from os.path import *\nprint(join('a', 'b'))\n") == []


def test_undefined_names_come_with_the_import_that_fits():
    view = view_of({
        "backend/manage.py": "",
        "backend/menu/models.py": "from django.db import models\n\n\nclass MenuItemAddOn(models.Model):\n    pass\n",
        "backend/menu/views.py": "def q():\n    return models.Prefetch('x', MenuItemAddOn.objects.all())\n",
        "backend/orders/serializers.py": "def f():\n    return MenuItemAddOn\n",
        "backend/orders/status.py": "def status():\n    return 1\n",
    })
    details = {p["detail"] for p in checks.undefined_name_problems(view, ["backend/menu/views.py",
                                                                           "backend/orders/serializers.py"])}
    assert "line 2: 'models' is used but never defined or imported (missing `from django.db import models`?)" in details
    assert any("`from .models import MenuItemAddOn`" in d for d in details)            # same app
    assert any("`from menu.models import MenuItemAddOn`" in d for d in details)        # another app, from backend/


def test_import_fixes_insert_after_the_existing_imports():
    files = {"app/__init__.py": "", "app/models.py": "class Thing:\n    pass\n",
             "app/views.py": '"""Views."""\nimport os\n\n\ndef show():\n    return json.dumps(Thing().x), os.sep\n'}
    fixes = autofix.import_fixes(view_of(files), ["app/views.py"])
    assert len(fixes) == 1
    assert fixes[0].content.startswith('"""Views."""\nimport os\nimport json\nfrom .models import Thing\n')
    compile(fixes[0].content, "views.py", "exec")


# ---------------------------------------------------------------- dependencies
PY_PROJECT = {
    "backend/requirements.txt": "Django>=5.0\n",
    "backend/manage.py": "",
    "backend/shop/settings.py": (
        "ROOT_URLCONF = 'shop.urls'\nINSTALLED_APPS = ['corsheaders', 'rest_framework', 'shop']\n"
        "DATABASES = {'default': {'ENGINE': 'django.db.backends.postgresql'}}\n"),
    "backend/shop/urls.py": "urlpatterns = []\n",
    "backend/shop/views.py": (
        "import requests\nfrom django.http import JsonResponse\n\ntry:\n    import ujson\nexcept ImportError:\n"
        "    ujson = None\n"),
    "Dockerfile": 'FROM python:3.12-slim\nCMD ["gunicorn", "shop.wsgi:application"]\n',
}


def test_packages_the_code_needs_must_be_listed():
    problems = checks.python_dependency_problems(view_of(PY_PROJECT))
    assert len(problems) == 1 and problems[0]["path"] == "backend/requirements.txt"
    detail = problems[0]["detail"]
    for needed in ("django-cors-headers", "djangorestframework", "psycopg[binary]", "requests", "gunicorn"):
        assert needed in detail
    assert "ujson" not in detail and "asgiref" not in detail     # optional import; Django brings asgiref


def test_requirements_are_completed_without_a_model():
    fixes = autofix.requirement_fixes(view_of(PY_PROJECT), None)
    assert fixes[0].content == ("Django>=5.0\ndjango-cors-headers\ndjangorestframework\ngunicorn\n"
                                "psycopg[binary]\nrequests\n")


FRONTEND = {
    "frontend/index.html": '<script type="module" src="/src/main.jsx"></script>\n',
    "frontend/vite.config.js": ("import { defineConfig } from 'vite';\nimport react from '@vitejs/plugin-react';\n"
                                "export default defineConfig({ plugins: [react()] });\n"),
    "frontend/src/main.jsx": ("import React from 'react';\nimport { createRoot } from 'react-dom/client';\n"
                              "import { BrowserRouter } from 'react-router-dom';\nimport App from './App.jsx';\n"),
    "frontend/src/App.jsx": "import axios from 'axios';\nimport { thing } from '@acme/widgets/x';\nexport default 1;\n",
}


def test_a_frontend_without_package_json_is_caught_and_created():
    problems = checks.js_dependency_problems(view_of(FRONTEND))
    assert [p["path"] for p in problems] == ["frontend/package.json"]
    assert "has no package.json" in problems[0]["detail"]
    fixes = autofix.package_json_fixes(view_of(FRONTEND), None)
    data = json.loads(fixes[0].content)
    assert data["scripts"] == {"dev": "vite", "build": "vite build", "preview": "vite preview"}
    assert data["dependencies"] == {"@acme/widgets": "latest", "axios": "^1.7.7", "react": "^18.3.1",
                                    "react-dom": "^18.3.1", "react-router-dom": "^6.26.2"}
    assert set(data["devDependencies"]) == {"vite", "@vitejs/plugin-react"}
    assert data["type"] == "module"


def test_index_html_must_load_files_that_exist():
    files = {**FRONTEND, "frontend/public/favicon.svg": "<svg/>",
             "frontend/index.html": ('<link rel="icon" href="/favicon.svg">\n<link href="/src/missing.css">\n'
                                     '<script type="module" src="/src/main.jsx"></script>\n')}
    problems = checks.html_problems(view_of(files))
    assert [p["detail"] for p in problems] == ["line 2: loads '/src/missing.css', but frontend/src/missing.css "
                                               "does not exist"]


# ---------------------------------------------------------------- Dockerfiles
DOCKER_PROJECT = {
    "backend/manage.py": "", "backend/shop/__init__.py": "", "backend/shop/wsgi.py": "application = None\n",
    "frontend/package.json": '{"scripts": {"build": "vite build"}}',
    "frontend/vite.config.js": "export default {};\n",
    "Dockerfile": (
        "FROM node:20-alpine AS web\nWORKDIR /app/frontend\nCOPY frontend/package*.json ./\nRUN npm ci\n"
        "COPY frontend/ .\nRUN npm run build\n\n"
        "FROM python:3.12-slim\nWORKDIR /app\nCOPY backend/ ./backend\n"
        "COPY --from=web /app/frontend/build ./static\nCOPY entrypoint.sh /entrypoint.sh\n"
        "COPY supervisord.conf /etc/supervisor/conf.d/app.conf\nCMD [\"/entrypoint.sh\"]\n"),
    "entrypoint.sh": ("#!/bin/sh\npython manage.py migrate\ncd backend && python manage.py collectstatic --noinput\n"
                      "exec gunicorn shop.wsgi:application --bind 0.0.0.0:8000\n"),
}


def test_dockerfile_problems_are_found_without_building():
    problems = checks.dockerfile_problems(view_of(DOCKER_PROJECT))
    found = {(p["path"], p["detail"].split(",")[0][:60]) for p in problems}
    details = " | ".join(p["detail"] for p in problems)
    assert ("supervisord.conf", "Dockerfile line 13 copies 'supervisord.conf'") in found
    assert "runs `npm ci`, which needs frontend/package-lock.json" in details
    assert "copies /app/frontend/build from stage 'web', but that build writes to /app/frontend/dist" in details
    assert ("line 2 runs `python manage.py migrate` in /app, but the image has no /app/manage.py "
            "(manage.py is at /app/backend/manage.py in the image)") in details
    assert "collectstatic" not in details and "gunicorn" not in details     # after `cd backend` both are right
    assert len(problems) == 4


def test_dockerfile_fixes_are_made_without_a_model():
    fixes = autofix.dockerfile_fixes(view_of(DOCKER_PROJECT), None)
    assert "RUN npm install\n" in fixes[0].content
    assert "COPY --from=web /app/frontend/dist ./static\n" in fixes[0].content


# ---------------------------------------------------------------- Django boilerplate
def test_django_boilerplate_is_created_from_the_settings():
    files = {"backend/shop/settings.py": ("ROOT_URLCONF = 'shop.urls'\nWSGI_APPLICATION = 'shop.wsgi.application'\n"
                                          "ASGI_APPLICATION = 'shop.asgi.app'\nINSTALLED_APPS = []\n"),
             "backend/shop/urls.py": "urlpatterns = []\n"}
    fixes = {f.path: f for f in autofix.django_fixes(view_of(files), None)}
    assert set(fixes) == {"backend/manage.py", "backend/shop/wsgi.py", "backend/shop/asgi.py"}
    assert '"DJANGO_SETTINGS_MODULE", "shop.settings"' in fixes["backend/manage.py"].content
    assert "execute_from_command_line(sys.argv)" in fixes["backend/manage.py"].content
    assert "app = get_asgi_application()" in fixes["backend/shop/asgi.py"].content
    for fix in fixes.values():
        compile(fix.content, fix.path, "exec")


def test_manage_py_that_drops_the_subcommand_is_fixed():
    files = {"manage.py": "import sys\nfrom django.core.management import execute_from_command_line\n"
                          "execute_from_command_line(sys.argv[1:])\n"}
    fixes = autofix.django_fixes(view_of(files), None)
    assert fixes[0].content.endswith("execute_from_command_line(sys.argv)\n")


# ---------------------------------------------------------------- brief and changelog
def report_for(tmp_path, **extra) -> RunReport:
    defaults = dict(run_id="20261125-000000-abc123", status="completed", task="Add a calculator\nwith tests",
                    workspace=str(tmp_path), invocation_source="cli", context_strategy="markdown", team=[],
                    changes=[FileChange("calc.py", "create", "x = 1\n", "python_developer"),
                             FileChange("README.md", "modify", "# x\n", "python_developer"),
                             FileChange("requirements.txt", "create", "requests\n", "forge_tools")],
                    verification=VerificationResult(True, ["after repair attempt 1"]),
                    warnings=["backend/x.py: this change removes route 'a/'"],
                    test_run=[{"command": "python calc.py", "timed_out": False, "stderr": "", "reported": "",
                               "skipped": "", "unavailable": ""},
                              {"command": "python calc.py 1 0", "timed_out": False,
                               "stderr": "Traceback (most recent call last):\n", "reported": "", "skipped": "",
                               "unavailable": ""}])
    defaults.update(extra)
    return RunReport(**defaults)


def test_the_changelog_entry_comes_from_the_run_report(tmp_path):
    entry = projectdocs.changelog_entry(report_for(tmp_path))
    assert "- run 20261125-000000-abc123" in entry and "**Task:** Add a calculator with tests" in entry
    assert "- Created: `calc.py`, `requirements.txt` (tools)" in entry
    assert "- Changed: `README.md`" in entry
    assert "- Checks: files gate passed after 1 repair round(s), test run: 2 command(s), 1 failed" in entry
    assert "  - test command failed: python calc.py 1 0" in entry


def test_new_changelog_entries_go_first():
    older = "# Changelog\n\nIntro.\n\n## 2026-01-01 - run a\n<!-- forge-run: a -->\nold\n"
    updated = projectdocs.updated_changelog(older, "## new\n<!-- forge-run: b -->\n")
    assert updated.index("## new") < updated.index("## 2026-01-01")
    assert updated.startswith("# Changelog\n\nIntro.\n\n## new")
    assert projectdocs.updated_changelog(None, "## e\n").startswith("# Changelog\n")
    assert projectdocs.updated_changelog("notes\n", "## e\n") == "## e\n\nnotes\n"


def test_the_next_run_gets_what_the_last_run_left(tmp_path):
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a + b + missing\n")
    workspace = Workspace(tmp_path)
    notes = projectdocs.record_run(workspace, report_for(tmp_path), tmp_path / ".coding-agent" / "runs" / "r1")
    assert notes == ["CHANGELOG.md: added the entry for run 20261125-000000-abc123", ".coding-agent/PROJECT.md: rewritten"]
    assert (tmp_path / "CHANGELOG.md").read_text().startswith("# Changelog")
    assert projectdocs.leftover(workspace) == ["- backend/x.py: this change removes route 'a/'",
                                              "- test command failed: python calc.py 1 0"]
    brief = projectdocs.brief(workspace)
    assert brief.open_issues == 1 and brief.left_open == 2 and brief.history
    assert "[undefined-names] calc.py: line 2: 'missing' is used but never defined" in brief.text
    assert "## Left open by the last run" in brief.text and "### 2026-" in brief.text


# ---------------------------------------------------------------- sandbox env
def test_the_test_run_gets_the_values_from_env_example(tmp_path):
    (tmp_path / "backend").mkdir()
    (tmp_path / ".env.example").write_text("# settings\nSECRET_KEY='dev-key'\nDEBUG=True\nPATH=/nope\n")
    (tmp_path / "backend" / ".env.example").write_text("export DB_NAME=shop\nDEBUG=False\n")
    env = testrun.sandbox_env(tmp_path)
    assert env["SECRET_KEY"] == "dev-key" and env["DEBUG"] == "True" and env["DB_NAME"] == "shop"
    assert env["PATH"] != "/nope"


def test_migrations_need_django_installed(tmp_path):
    changes = ChangeSet(Workspace(tmp_path))
    changes.add(FileChange("manage.py", "create", "x = 1\n"))
    assert testrun.generate_migrations(changes, testrun.PythonEnv("python")) == ({}, "")


# ---------------------------------------------------------------- runs
def test_tools_fix_imports_and_requirements_before_any_repair(tmp_path):
    views = "import requests\n\n\ndef fetch(url):\n    return json.loads(requests.get(url).text)\n"
    llm = ScriptedLLM({"python_developer": {"summary": "fetcher", "files": [
        {"path": "fetcher.py", "action": "create", "content": views}]}})
    report = CodingAgent.with_client(llm).unit("python_developer").run(
        CodingRequest(task="fetch JSON", workspace=str(tmp_path)), UI)
    assert report.status == "completed", report.error
    assert llm.roles_called() == ["python_developer"]               # no self-check round, no repair
    assert (tmp_path / "fetcher.py").read_text().startswith("import requests\nimport json\n")
    assert (tmp_path / "requirements.txt").read_text() == "requests\n"
    assert {n.split(":")[0] for n in report.notes} >= {"fixed by tools"}
    assert "test run skipped: CODING_AGENT_TEST_RUN=off, so the generated code was never run" in report.warnings
    changelog = (tmp_path / "CHANGELOG.md").read_text()
    assert "**Task:** fetch JSON" in changelog and "`requirements.txt` (tools)" in changelog
    assert (tmp_path / ".coding-agent" / "PROJECT.md").is_file()


def test_a_builder_fixes_its_own_mistakes_first(tmp_path):
    broken = "def total(items):\n    return sum(price_of(i) for i in items)\n"
    fixed = "def price_of(item):\n    return item['price']\n\n\n" + broken

    def builder(prompt, schema):
        content = fixed if "## Problems to fix" in prompt else broken
        return {"summary": "totals", "files": [{"path": "totals.py", "action": "create", "content": content}]}

    llm = ScriptedLLM({"python_developer": builder})
    report = CodingAgent.with_client(llm).unit("python_developer").run(
        CodingRequest(task="sum prices", workspace=str(tmp_path)), UI)
    assert report.status == "completed", report.error
    assert llm.roles_called() == ["python_developer", "python_developer"]
    second = llm.calls[1]["prompt"]
    assert "- totals.py [undefined-names]: line 2: 'price_of' is used but never defined or imported" in second
    assert "def price_of" in (tmp_path / "totals.py").read_text()


def test_an_empty_plan_leaves_the_work_to_the_other_division(tmp_path):
    llm = ScriptedLLM({
        "engineering_director": {"rationale": "containers", "team": ["python_developer", "docker_engineer"]},
        "tech_lead": {"summary": "nothing for development", "work_items": []},
        "docker_engineer": {"summary": "image", "files": [
            {"path": "Dockerfile", "action": "create", "content": "FROM python:3.12-slim\n"}]},
    })
    report = CodingAgent.with_client(llm).run(CodingRequest(task="Containerize the app", workspace=str(tmp_path)), UI)
    assert report.status == "completed", report.error
    assert "python_developer" not in llm.roles_called()
    assert [c.path for c in report.changes] == ["Dockerfile"]
    lead = next(o for o in report.outputs if o.role == "tech_lead")
    assert "left this request to the other divisions" in lead.concerns[-1]


def test_a_lead_that_cannot_plan_stops_the_run(tmp_path):
    def broken_lead(prompt, schema):
        raise LLMError("The model did not return valid JSON")

    llm = ScriptedLLM({
        "engineering_director": {"rationale": "app", "team": ["python_developer", "frontend_developer"]},
        "tech_lead": broken_lead,
    })
    report = CodingAgent.with_client(llm).run(CodingRequest(task="Build the app", workspace=str(tmp_path)), UI)
    assert report.status == "failed"
    assert report.error.startswith("No usable plan from Tech Lead (Development): The model did not return valid JSON")
    assert llm.roles_called().count("tech_lead") == 2                # one retry
    assert "python_developer" not in llm.roles_called() and "frontend_developer" not in llm.roles_called()
    assert not (tmp_path / "CHANGELOG.md").exists()


def test_planners_see_the_brief(tmp_path):
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a + b + missing\n")
    projectdocs.record_run(Workspace(tmp_path), report_for(tmp_path), tmp_path / ".coding-agent" / "runs" / "r1")
    llm = ScriptedLLM()
    CodingAgent.with_client(llm).unit("software_architect").run(
        CodingRequest(task="Plan the next step", workspace=str(tmp_path)), UI)
    prompt = llm.calls[0]["prompt"]
    assert "### Project brief (read from the code" in prompt
    assert "'missing' is used but never defined" in prompt
    assert "test command failed: python calc.py 1 0" in prompt


# ---------------------------------------------------------------- import check
def test_every_package_module_is_imported_in_the_sandbox(tmp_path):
    import sys

    changes = ChangeSet(Workspace(tmp_path))
    for path, content in {"app/__init__.py": "", "app/good.py": "VALUE = 1\n",
                          "app/bad.py": "from os import nothing_here\n",
                          "script.py": "raise SystemExit('a script: never imported')\n"}.items():
        changes.add(FileChange(path, "create", content))
    result = testrun.import_check(changes, testrun.PythonEnv(sys.executable))
    assert result.ran and result.modules == ["app", "app.bad", "app.good"]
    assert len(result.failures) == 1
    failure = result.failures[0]
    assert failure["path"] == "app/bad.py" and failure["changed"]
    assert failure["detail"].startswith("importing app.bad fails: ImportError: cannot import name 'nothing_here'")


def test_django_modules_are_imported_from_where_manage_py_runs():
    files = {"backend/manage.py", "backend/shop/__init__.py", "backend/shop/settings.py", "backend/shop/wsgi.py",
             "backend/menu/models.py", "backend/menu/migrations/0001_initial.py", "backend/menu/tests.py",
             "frontend/vite.config.py", "tools/my-script.py"}
    modules = [m for m, _ in testrun.importable_modules(files, "backend", django=True)]
    assert modules == ["menu.models", "menu.tests", "shop", "shop.settings"]
    script = testrun.IMPORT_CHECK_SCRIPT.format(
        failed="F", end="E", modules=modules, setup=testrun.DJANGO_SETUP.format(settings="shop.settings"),
        after=testrun.DJANGO_AFTER)
    compile(script, "import_check.py", "exec")


def test_a_module_that_fails_to_import_goes_to_repair(tmp_path, monkeypatch):
    monkeypatch.setenv("CODING_AGENT_TEST_RUN", "repair")

    def debugger(prompt, schema):
        if "commands" in (schema or {}).get("properties", {}):
            return {"summary": "nothing to run", "commands": []}
        return {"summary": "fixed", "files": [{"path": "app/core.py", "action": "create",
                                               "content": "from os import sep\n"}]}

    llm = ScriptedLLM({
        "python_developer": {"summary": "core", "files": [
            {"path": "app/__init__.py", "action": "create", "content": ""},
            {"path": "app/core.py", "action": "create", "content": "from os import nothing_here\n"}]},
        "debugging_specialist": debugger,
    })
    report = CodingAgent.with_client(llm).unit("python_developer").run(
        CodingRequest(task="core module", workspace=str(tmp_path)), UI)
    assert report.status == "completed", report.error
    repair = next(c["prompt"] for c in llm.calls if c["role"] == "debugging_specialist" and "Problems to fix" in c["prompt"])
    assert "- app/core.py [import-run]: importing app.core fails: ImportError: cannot import name 'nothing_here'" in repair
    assert (tmp_path / "app/core.py").read_text() == "from os import sep\n"
    assert not [w for w in report.warnings if w.startswith("import check")]


# ---------------------------------------------------------------- requirements across runs
def test_requirements_are_kept_for_later_runs(tmp_path):
    workspace = Workspace(tmp_path)
    (tmp_path / "app.py").write_text("x = 1\n")
    first = report_for(tmp_path, run_id="run-1", constraints=["Django 5"],
                       acceptance_criteria=["The API is under /api/", "Orders survive restarts"])
    projectdocs.record_run(workspace, first, tmp_path / "r1")
    second = report_for(tmp_path, run_id="run-2", constraints=[], acceptance_criteria=["the API is under /api/"])
    projectdocs.record_run(workspace, second, tmp_path / "r2")
    assert projectdocs.requirements(workspace) == [
        "- [constraint] Django 5 (run run-1)", "- [acceptance] Orders survive restarts (run run-1)",
        "- [acceptance] the API is under /api/ (run run-2)"]
    brief = projectdocs.brief(workspace)
    assert brief.requirements == 3 and brief.text.startswith("## Requirements from earlier runs")
    facts = projectdocs.facts_for_files(projectdocs.workspace_view(workspace), ["app.py"],
                                        required=projectdocs.requirements(workspace))
    assert "Orders survive restarts" in facts
    changelog = (tmp_path / "CHANGELOG.md").read_text()
    assert "**Constraints:** Django 5" in changelog
    assert "**Acceptance criteria:** The API is under /api/; Orders survive restarts" in changelog


def test_nothing_left_open_is_not_counted_as_an_item(tmp_path):
    workspace = Workspace(tmp_path)
    (tmp_path / "app.py").write_text("x = 1\n")
    projectdocs.record_run(workspace, report_for(tmp_path, warnings=[], test_run=[]), tmp_path / "r1")
    assert "- nothing" in (tmp_path / ".coding-agent" / "PROJECT.md").read_text()
    assert projectdocs.leftover(workspace) == []


def test_the_next_runs_builders_see_earlier_requirements(tmp_path):
    first = ScriptedLLM({"python_developer": {"summary": "api", "files": [
        {"path": "api.py", "action": "create", "content": "PREFIX = '/api/'\n"}]}})
    CodingAgent.with_client(first).unit("python_developer").run(
        CodingRequest(task="api module", workspace=str(tmp_path), acceptance_criteria=["The API is under /api/"]), UI)
    second = ScriptedLLM({"python_developer": {"summary": "more", "files": [
        {"path": "more.py", "action": "create", "content": "y = 2\n"}]}})
    CodingAgent.with_client(second).unit("python_developer").run(
        CodingRequest(task="Fix whatever the problem is", workspace=str(tmp_path)), UI)
    prompt = second.calls[0]["prompt"]
    assert "### Requirements from earlier runs" in prompt
    assert "- [acceptance] The API is under /api/ (run " in prompt


# ---------------------------------------------------------------- CLI
def test_check_and_fix_commands(tmp_path, capsys):
    from coding_agent import cli

    (tmp_path / "app.py").write_text("import requests\n\n\ndef get():\n    return requests, os.sep\n")
    assert cli.main(["check", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "app.py [undefined-names] line 5: 'os'" in out and "requirements.txt [dependencies]" in out
    assert cli.main(["fix", str(tmp_path), "--yes"]) == 0
    assert (tmp_path / "requirements.txt").read_text() == "requests\n"
    assert (tmp_path / "app.py").read_text().startswith("import requests\nimport os\n")
    assert "Fix what the tools can fix" in (tmp_path / "CHANGELOG.md").read_text()
    assert cli.main(["check", str(tmp_path)]) == 0
