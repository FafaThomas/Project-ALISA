"""Fixes from the second burger-shop build: Django projects without __init__.py or manage.py,
backup folders, work items too big for one answer, idle specialists, and a hosted fallback model."""

from conftest import ScriptedLLM

from coding_agent import CodingAgent, CodingRequest, InvocationContext, InvocationSource
from coding_agent.core.llm import FallbackClient, ModelRouter, OutputLimitError, RateLimitError
from coding_agent.core.models import FileChange
from coding_agent.core.verification import verify_files
from coding_agent.core.workspace import ChangeSet, Workspace

UI = InvocationContext(source=InvocationSource.UI)
SETTINGS = ("ROOT_URLCONF = 'burgershop.urls'\nWSGI_APPLICATION = 'burgershop.wsgi.application'\n"
            "INSTALLED_APPS = ['django.contrib.admin', 'menu', 'orders']\n")


def gate(tmp_path, files):
    changes = ChangeSet(Workspace(tmp_path))
    for path, content in files.items():
        changes.add(FileChange(path, "create", content))
    return {(p["path"], p["check"]): p["detail"] for p in verify_files(changes, []).problems}


# ---------------------------------------------------------------- 1. django projects without __init__.py
def test_a_django_project_without_init_files_or_manage_py_is_caught(tmp_path):
    problems = gate(tmp_path, {
        "backend/burgershop/settings.py": SETTINGS,
        "backend/burgershop/urls.py": "from menu.views import index\nurlpatterns = []\n",
        "backend/menu/views.py": "def index(request):\n    pass\n",
        "backend/orders/models.py": "x = 1\n",
    })
    # Filed under the file to create, not the settings file that mentions it.
    wsgi = problems[("backend/burgershop/wsgi.py", "references")]
    assert "backend/burgershop/settings.py line 2: WSGI_APPLICATION" in wsgi and "Create backend/burgershop/wsgi.py" in wsgi
    assert "has no manage.py; create backend/manage.py" in problems[("backend/manage.py", "written")]
    assert "the code needs: django" in problems[("requirements.txt", "dependencies")]
    assert len(problems) == 3  # 'menu' and 'orders' are folders of modules: importable without __init__.py


def test_a_missing_wsgi_is_created_by_the_tools_without_a_model(tmp_path):
    llm = ScriptedLLM({
        "python_developer": {"summary": "settings", "files": [
            {"path": "backend/manage.py", "action": "create", "content": "import os\n"},
            {"path": "backend/burgershop/settings.py", "action": "create", "content": SETTINGS},
            {"path": "backend/burgershop/urls.py", "action": "create", "content": "urlpatterns = []\n"},
            {"path": "backend/menu/views.py", "action": "create", "content": "x = 1\n"},
            {"path": "backend/orders/models.py", "action": "create", "content": "x = 1\n"}]},
    })
    report = CodingAgent.with_client(llm).unit("python_developer").run(
        CodingRequest(task="Django project", workspace=str(tmp_path)), UI)
    assert report.status == "completed", report.error
    assert "debugging_specialist" not in llm.roles_called()
    wsgi = (tmp_path / "backend/burgershop/wsgi.py").read_text()
    assert 'os.environ.setdefault("DJANGO_SETTINGS_MODULE", "burgershop.settings")' in wsgi
    assert "application = get_wsgi_application()" in wsgi
    assert (tmp_path / "backend/requirements.txt").read_text() == "django\n"
    assert any("backend/burgershop/wsgi.py" in n for n in report.notes)


def test_leads_plan_framework_boilerplate():
    from coding_agent.core.prompts import _KIND_INSTRUCTIONS, _LEAD_INSTRUCTIONS
    for text in (_LEAD_INSTRUCTIONS, _KIND_INSTRUCTIONS["coordinator"]):
        assert "manage.py" in text and "wsgi.py" in text and "__init__.py" in text


def test_a_complete_django_project_passes(tmp_path):
    assert gate(tmp_path, {
        "backend/manage.py": "import os\n",
        "backend/requirements.txt": "Django>=5.0\n",
        "backend/burgershop/settings.py": SETTINGS,
        "backend/burgershop/wsgi.py": "application = None\n",
        "backend/burgershop/urls.py": "urlpatterns = []\n",
        "backend/menu/views.py": "x = 1\n",
        "backend/orders/models.py": "x = 1\n",
    }) == {}


# ---------------------------------------------------------------- 4. backup folders
def test_backup_folders_are_not_part_of_the_project(tmp_path):
    for rel in ("app.py", "burger-shop.bak/app.py", "backup/app.py", "old/app.py", "src.orig/x.py", "src/x.py"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("x = 1\n")
    assert Workspace(tmp_path).list_files() == ["app.py", "src/x.py"]


# ---------------------------------------------------------------- 2. work items too big for one answer
PAGES = ["src/App.jsx", "src/pages/Menu.jsx", "src/pages/Item.jsx"]


def page_builder(calls):
    def handle(prompt, schema):
        target = next((p for p in PAGES if f"In THIS response write only {p}" in prompt), None)
        calls.append(target)
        if target is None:
            raise OutputLimitError("Output hit CODING_AGENT_OLLAMA_NUM_PREDICT (8192) before the JSON finished")
        return {"summary": f"wrote {target}", "files": [
            {"path": target, "action": "create", "content": "export default function X() { return null; }\n"}]}
    return handle


def frontend_run(tmp_path, files, calls):
    llm = ScriptedLLM({
        "engineering_director": {"rationale": "x", "team": ["tech_lead", "frontend_developer"]},
        "tech_lead": {"summary": "plan", "work_items": [
            {"id": "W1", "title": "pages", "owner_role": "frontend_developer", "description": "pages",
             "files": files}]},
        "frontend_developer": page_builder(calls),
    })
    return CodingAgent.with_client(llm).run(CodingRequest(task="React pages", workspace=str(tmp_path)), UI), llm


def test_items_with_three_or_more_files_are_written_one_file_per_call(tmp_path):
    calls = []
    report, _ = frontend_run(tmp_path, PAGES, calls)
    assert report.status == "completed", report.error
    assert calls == PAGES
    assert sorted(c.path for c in report.changes) == sorted(PAGES)


def test_an_answer_cut_off_by_the_output_limit_is_retried_file_by_file(tmp_path):
    calls = []
    report, _ = frontend_run(tmp_path, PAGES[:2], calls)
    assert report.status == "completed", report.error
    assert calls == [None, *PAGES[:2]]  # one combined try that ran out, then one call per file


def test_repairs_of_many_files_go_one_file_at_a_time(tmp_path):
    calls = []
    llm = ScriptedLLM({
        "engineering_director": {"rationale": "x", "team": ["tech_lead", "frontend_developer"]},
        "tech_lead": {"summary": "plan", "work_items": [
            {"id": "W1", "title": "pages", "owner_role": "frontend_developer", "description": "pages",
             "files": PAGES[:2]},
            {"id": "W2", "title": "page", "owner_role": "frontend_developer", "description": "page",
             "files": PAGES[2:]}]},
        "frontend_developer": {"summary": "ran out", "files": []},   # writes nothing: three missing files
        "debugging_specialist": page_builder(calls),
    })
    report = CodingAgent.with_client(llm).run(CodingRequest(task="React pages", workspace=str(tmp_path)), UI)
    assert report.status == "completed", report.error
    assert sorted(c for c in calls if c) == sorted(PAGES) and None not in calls


def test_leads_are_told_to_keep_work_items_small():
    from coding_agent.core.prompts import _KIND_INSTRUCTIONS, _LEAD_INSTRUCTIONS
    assert "at most 3 files" in _LEAD_INSTRUCTIONS and "at most 3 files" in _KIND_INSTRUCTIONS["coordinator"]


# ---------------------------------------------------------------- 3. specialists with nothing to do
def test_idle_specialists_are_told_what_is_taken_and_that_nothing_is_fine(tmp_path):
    llm = ScriptedLLM({
        "engineering_director": {"rationale": "x", "team": ["tech_lead", "python_developer",
                                                            "infrastructure_engineer"]},
        "tech_lead": {"summary": "plan", "work_items": [
            {"id": "W1", "title": "api", "owner_role": "python_developer", "description": "api",
             "files": ["api.py", "views.py"]}]},
        "python_developer": {"summary": "api", "files": [
            {"path": "api.py", "action": "create", "content": "x = 1\n"},
            {"path": "views.py", "action": "create", "content": "y = 1\n"}]},
        "infrastructure_engineer": {"summary": "rewrote everything", "files": [
            {"path": "api.py", "action": "modify", "content": "x = 2\n"},
            {"path": "views.py", "action": "modify", "content": "y = 2\n"}]},
    })
    report = CodingAgent.with_client(llm).run(CodingRequest(task="Add an API", workspace=str(tmp_path)), UI)
    prompt = next(c["prompt"] for c in llm.calls if c["role"] == "infrastructure_engineer")
    assert "may not change them: api.py, views.py" in prompt and "empty files list" in prompt
    rejected = [w for w in report.warnings if "infrastructure_engineer" in w]
    assert rejected == ["infrastructure_engineer (build): rejected 2 file(s) owned by other work items: "
                        "api.py, views.py"]


# ---------------------------------------------------------------- 5. hosted fallback before local
class Down:
    def __init__(self, model):
        self.model = model

    def generate_json(self, system, prompt, **kwargs):
        raise RateLimitError(f"{self.model} daily quota spent")

    def unload(self):
        return True


def router_with(monkeypatch, up: set[str]):
    monkeypatch.setenv("FORGE_GEMINI_GENERAL_MODEL", "gemini-3.5-flash-lite")
    monkeypatch.setenv("FORGE_GEMINI_FALLBACK_MODEL", "@general")
    monkeypatch.setenv("FORGE_CODING_AGENT_PLANNING_MODEL", "qwen3:8b")

    def factory(model):
        return ScriptedLLM({}, model=model) if model in up or not model.startswith("gemini:") else Down(model)

    return ModelRouter(client_factory=factory)


def test_a_hosted_model_out_of_quota_tries_the_hosted_fallback_first(monkeypatch):
    client, _ = router_with(monkeypatch, {"gemini:gemini-3.5-flash-lite"}).resolve(
        "planning", "gemini:gemini-2.5-flash", "unit")
    client.generate_json("You are the Engineering Director of ALISA Forge", "p", schema=None)
    assert isinstance(client, FallbackClient)
    assert client.used_model == "gemini:gemini-3.5-flash-lite" and "daily quota" in client.fallback_reason
    assert client.fallback_kind == "hosted"


def test_when_both_hosted_models_are_down_the_local_model_answers(monkeypatch):
    client, _ = router_with(monkeypatch, set()).resolve("planning", "gemini:gemini-2.5-flash", "unit")
    client.generate_json("You are the Engineering Director of ALISA Forge", "p", schema=None)
    assert client.used_model == "qwen3:8b" and client.fallback_kind == "local"


def test_a_unit_already_on_the_fallback_model_goes_straight_to_local(monkeypatch):
    client, _ = router_with(monkeypatch, set()).resolve("planning", "gemini:@general", "unit")
    assert not isinstance(client.primary, FallbackClient)


# ---------------------------------------------------------------- test run: requirements and printed import errors
def test_a_pinned_version_that_does_not_exist_is_a_requirements_problem(tmp_path):
    from coding_agent.core import testrun
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "requirements.txt").write_text("Django==5.0.4\npsycopg[binary]==3.1.10\n")
    changes = ChangeSet(Workspace(tmp_path))
    pip = ('ERROR: Could not find a version that satisfies the requirement psycopg-binary==3.1.10; extra == "binary" '
           "(from psycopg[binary]) (from versions: 3.1.12, 3.2.0, 3.3.9)\nERROR: No matching distribution found")
    path, problem = testrun.unavailable_pin(pip, ["backend/requirements.txt"], changes)
    assert path == "backend/requirements.txt"
    assert "no psycopg-binary==3.1.10" in problem and "3.1.12, 3.2.0, 3.3.9" in problem
    # no versions at all (no wheel) or a network error: only a warning
    assert testrun.unavailable_pin(pip.replace("3.1.12, 3.2.0, 3.3.9", "none"), ["backend/requirements.txt"],
                                   changes) == ("", "")
    assert testrun.unavailable_pin("ERROR: connection timed out", ["backend/requirements.txt"], changes) == ("", "")


def test_a_bad_pin_goes_to_repair_before_any_command_runs(tmp_path, monkeypatch):
    from coding_agent.core import testrun
    monkeypatch.setenv("CODING_AGENT_TEST_RUN", "repair")
    pinned = {"value": "psycopg[binary]==3.1.10"}

    def fake_prepare(changes):
        text = changes.current("requirements.txt") or ""
        if "3.1.10" in text:
            return testrun.PythonEnv("py", warning="install failed", packages=("x",),
                                     problem_path="requirements.txt", problem="no psycopg-binary==3.1.10")
        return testrun.PythonEnv(__import__("sys").executable)

    monkeypatch.setattr(testrun, "prepare_python", fake_prepare)
    llm = ScriptedLLM({
        "python_developer": {"summary": "app", "files": [
            {"path": "app.py", "action": "create", "content": "print('hi')\n"},
            {"path": "requirements.txt", "action": "create", "content": pinned["value"] + "\n"}]},
        "debugging_specialist": lambda prompt, schema: (
            {"summary": "plan", "commands": [{"command": "python app.py", "purpose": "runs"}]}
            if "commands" in (schema or {}).get("properties", {}) else
            {"summary": "fixed pin", "files": [
                {"path": "requirements.txt", "action": "modify", "content": "psycopg[binary]>=3.1.12\n"}]}),
    })
    report = CodingAgent.with_client(llm).unit("python_developer").run(
        CodingRequest(task="app", workspace=str(tmp_path)), UI)
    repair = [c["prompt"] for c in llm.calls if c["role"] == "debugging_specialist"
              and "commands" not in (c["schema"] or {}).get("properties", {})]
    assert repair and "- requirements.txt [test-run]: no psycopg-binary==3.1.10" in repair[0]
    assert report.status == "completed" and report.test_run and report.test_run[0]["exit_code"] == 0
    assert (tmp_path / "requirements.txt").read_text() == "psycopg[binary]>=3.1.12\n"


def test_a_friendly_could_not_find_message_counts_as_a_missing_package(tmp_path):
    from coding_agent.core import testrun
    result = testrun.CommandResult("python manage.py check", exit_code=1, stdout=(
        "Could not find Django. Is it installed and in your PYTHONPATH?\nSee: pip install django\n"))
    assert testrun.missing_package(result) == "django"
    result.reported = testrun.reported_errors(result.command, result)
    testrun.classify(result, testrun.PythonEnv("py", warning="install failed"), tmp_path, ".")
    assert not result.failed and "'django' is not installed" in result.unavailable

    with_deps = testrun.CommandResult("python manage.py check", exit_code=1, stdout=result.stdout)
    with_deps.reported = testrun.reported_errors(with_deps.command, with_deps)
    testrun.classify(with_deps, testrun.PythonEnv("py", packages=("Django",)), tmp_path, ".")
    assert with_deps.failed and "could not import module 'django'" in with_deps.reported


def test_a_manage_py_that_ignores_its_subcommand_fails(tmp_path):
    from coding_agent.core import testrun
    help_text = ["Type 'manage.py help <subcommand>' for help.", "", "Available subcommands:", "", "[django]",
                 "    check", "    runserver"]
    manage = "import sys\n\nfor line in " + repr(help_text) + ":\n    print(line)\n"
    changes = ChangeSet(Workspace(tmp_path / "ws"))
    changes.add(FileChange("backend/manage.py", "create", manage))
    result = testrun.run_commands(changes, [{"command": "python backend/manage.py check"},
                                            {"command": "python backend/manage.py help"}], 10)
    assert result[0].exit_code == 0 and result[0].failed and "sys.argv[1:]" in result[0].reported
    assert not result[1].failed
    problems = testrun.problems_from(result, changes, 10)
    assert [p["path"] for p in problems] == ["backend/manage.py"]


def test_django_modules_are_not_run_as_scripts(tmp_path):
    from coding_agent.core import testrun
    files = {
        "backend/manage.py": "from django.core.management import execute_from_command_line\n",
        "backend/orders/serializers.py": "from rest_framework import serializers\nfrom .models import Order\n",
        "backend/orders/services.py": "from .models import Order\n",
        "backend/tools/report.py": "import django\nos.environ.setdefault('X', 'y')\ndjango.setup()\n",
        "backend/orders/cli.py": "from .models import Order\n\nif __name__ == '__main__':\n    print(Order)\n",
        "script.py": "print('hello')\n",
    }
    for rel, content in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(content)
    _, _, why = testrun.to_argv("python backend/orders/serializers.py", tmp_path)
    assert "is a module of the Django project, not a script" in why
    assert "relative imports" in testrun.to_argv("python backend/orders/services.py", tmp_path)[2]
    for runnable in ("python backend/manage.py check", "python backend/tools/report.py",
                     "python backend/orders/cli.py", "python script.py"):
        assert testrun.to_argv(runnable, tmp_path)[0] is not None, runnable


def test_server_subcommands_are_refused(tmp_path):
    from coding_agent.core import testrun
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "manage.py").write_text("print('x')\n")
    argv, _, reason = testrun.to_argv("python backend/manage.py runserver 127.0.0.1:8000", tmp_path)
    assert argv is None and "runs until stopped" in reason
    assert testrun.to_argv("python backend/manage.py check", tmp_path)[0] is not None


def test_a_timeout_stops_child_processes_that_hold_the_output_open(tmp_path):
    """Django's autoreloader: the server runs in a child that inherits stdout. The timeout must not wait on it."""
    import time
    from coding_agent.core import testrun
    script = ("import subprocess, sys, time\n"
              "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
              "print('working', flush=True)\n"
              "time.sleep(120)\n")
    changes = ChangeSet(Workspace(tmp_path / "ws"))
    changes.add(FileChange("app.py", "create", script))
    started = time.monotonic()
    result = testrun.run_commands(changes, [{"command": "python app.py"}], 3)
    assert time.monotonic() - started < 30
    assert result[0].timed_out and result[0].failed and "working" in result[0].stdout


def test_a_server_that_started_and_was_stopped_is_not_a_hang(tmp_path):
    from coding_agent.core import testrun
    script = ("import time\n"
              "print('Starting development server at http://127.0.0.1:8000/', flush=True)\n"
              "time.sleep(120)\n")
    changes = ChangeSet(Workspace(tmp_path / "ws"))
    changes.add(FileChange("serve.py", "create", script))
    result = testrun.run_commands(changes, [{"command": "python serve.py"}], 3)
    assert result[0].timed_out and not result[0].failed and "server" in result[0].unavailable
    assert testrun.problems_from(result, changes, 3) == []


def test_the_test_plan_forbids_servers():
    from coding_agent.core.prompts import TEST_PLAN_INSTRUCTIONS
    assert "never start a server" in TEST_PLAN_INSTRUCTIONS
