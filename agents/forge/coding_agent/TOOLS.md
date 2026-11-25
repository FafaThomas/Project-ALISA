# The Coding Agent's Tools

The team has a **deterministic tools layer** that answers questions about the workspace and fixes problems with one right answer—no model needed. Each tool reads facts from the code, runs checks, or applies automated repairs.

## Architecture

### Where tools live

- **Core tools** (used by all units): `src/coding_agent/core/`
- **Unit-level tools** (optional, per unit): `<unit-folder>/tools.py` + `<unit-folder>/requirements.txt` (see below)

### How tools are called

**Before test run and repairs:**
- `units.py:_check()` calls the core tools early to fix and verify files before the model and test run.

**On every file change:**
- `specialist.py:_repair_call()` and `_build_call()` add project facts to builders' and repairers' context.

**Standalone:**
- CLI `coding-agent check` and `coding-agent fix` run the tools without a model.
- Python: `import projectfacts; projectfacts.ProjectView.of(changes)` or `import checks; problems = checks.project_problems(view)`.

### How they're integrated

1. **Core tools applied** (before model): `units.apply_tool_fixes(session, paths)` calls `deterministic_fixes()` and writes changes.
2. **Facts provided** to builders/repairers: `specialist.project_facts()` extracts facts using the core tools.
3. **Verification** after each specialist: `verification.verify_files()` runs checks after self-check rounds.
4. **Briefing** between runs: `projectdocs.brief()` and `projectdocs.record_run()` persist facts and requirements.

---

## Core Tools

### 1. Facts (`core/projectfacts.py`)

**Purpose:** Extract static facts from the workspace—no model needed, same facts every time.

**Entry points:**

| Function | Input | Output | Used by |
|---|---|---|---|
| `ProjectView.of(changes)` | ChangeSet | ProjectView (the workspace snapshot) | all checks, all facts extractions |
| `ProjectView.read(path)` | str (file path) | str (file content) or None | facts extraction, checks |
| `django_routes(changes, files)` | ChangeSet, file paths | list of `Route` | frontend dev (to know backend routes), checks (API contract) |
| `frontend_calls(changes, files)` | ChangeSet, file paths | list of `ApiCall` | backend dev (to know frontend calls), checks (API contract) |
| `python_manifest(view, python_root=None)` | ProjectView, optional root | dict (name → requirement) | builders (dependency facts), checks |
| `js_packages(view)` | ProjectView | dict of JS package info | builders, checks |
| `env_vars(view, root=None)` | ProjectView, optional root | dict (key → value or None) | builders (env facts) |
| `layout(view, root=None)` | ProjectView, optional root | dict (folder → paths) | builders (project structure) |

**Example:**

```python
from coding_agent.core.projectfacts import ProjectView

view = ProjectView.of(changes)
routes = view.django_routes(changes.files)  # or django_routes(changes, changes.files)
frontend_calls = view.frontend_calls(changes.files)
packages = python_manifest(view)
```

### 2. Checks (`core/checks.py`)

**Purpose:** Find problems with one definite answer—undefined names, missing imports, broken dependencies, Dockerfile paths, API mismatches.

**Entry points:**

| Function | Input | Output | Category | Soft/Hard |
|---|---|---|---|---|
| `undefined_names(source)` | str (Python source) | list of `(name, line, as_module)` | undefined names | hard |
| `used_before_definition(source)` | str (Python source) | list of `(name, line, defined_line)` | undefined names | hard |
| `python_root(view)` | ProjectView | str (root path, usually ".") | imports | — |
| `root_import_problems(view, changed_files)` | ProjectView, paths | list of `Problem` | imports | hard |
| `missing_python_packages(view, changed_files, *, python_paths=None)` | ProjectView, paths, optional roots | list of `Problem` | dependencies | hard |
| `missing_js_packages(view, changed_files)` | ProjectView, paths | list of `Problem` | dependencies | hard |
| `html_problems(view, changed_files)` | ProjectView, paths | list of `Problem` | HTML refs | hard |
| `dockerfile_problems(view, changed_files)` | ProjectView, paths | list of `Problem` | Dockerfile | soft |
| `api_contract_problems(view, changed_files)` | ProjectView, paths | list of `Problem` | API contract | soft |
| `project_problems(view, changed=None, *, python_paths=None)` | ProjectView, optional changed paths, optional roots | list of `Problem` | all of above | varies |

**Example:**

```python
from coding_agent.core.checks import project_problems
from coding_agent.core.projectfacts import ProjectView

view = ProjectView.of(changes)
all_problems = project_problems(view, changed=changes.files)
# Returns: [Problem(severity='hard', file=..., issue=..., suggestion=...)]
```

### 3. Fixes (`core/autofix.py`)

**Purpose:** Apply fixes that have one right answer—add missing packages, create boilerplate, strip run-relative imports, add missing imports.

**Entry points:**

| Function | Input | Output | Writes |
|---|---|---|---|
| `deterministic_fixes(view, changed, *, import_paths=None)` | ProjectView, changed files, optional roots | list of `Fix(path, content, reason)` | — (returns, doesn't write) |

**Example:**

```python
from coding_agent.core.autofix import deterministic_fixes

fixes = deterministic_fixes(view, changed=changes.files)
for fix in fixes:
    print(f"Fix {fix.path}: {fix.reason}")
    # write fix.content to fix.path
```

### 4. Migrations (`core/testrun.py`)

**Purpose:** Generate Django migrations in the sandbox before test commands run.

**Entry point:**

| Function | Input | Output | When |
|---|---|---|---|
| `generate_migrations(changes, env, seconds=90)` | ChangeSet, Environment, timeout | list of new `migrations` files | before test run |

### 5. Import Check (`core/testrun.py`)

**Purpose:** Import every project module in a sandbox, catching import-time failures like `from django.setup import setup`.

**Entry points:**

| Function | Input | Output | When |
|---|---|---|---|
| `import_check(changes, env, seconds=120)` | ChangeSet, Environment, timeout | `ImportCheck(modules, failures, ran, note)` | after test run |

### 6. Project Docs (`core/projectdocs.py`)

**Purpose:** Extract facts as Markdown for builders/repairers, write changelogs and brief for the next run.

**Entry points:**

| Function | Input | Output | Used by |
|---|---|---|---|
| `facts_for_files(view, files, budget=5000, required=None)` | ProjectView, paths, optional requirements | str (Markdown) | builders, repairers |
| `brief(workspace)` | Workspace | `Brief(text, open_issues, left_open, history, requirements)` | next run's analysts/planners |
| `changelog_entry(view, report, run_id, when=None)` | ProjectView, RunReport, run id, optional time | str (Markdown) | CHANGELOG.md |
| `record_run(workspace, report, run_dir)` | Workspace, RunReport, run folder | — | after each run, to write PROJECT.md + CHANGELOG.md |

**Example:**

```python
from coding_agent.core.projectdocs import facts_for_files, brief

facts_md = facts_for_files(view, files)  # → Markdown for this specialist's context
b = brief(workspace)  # → Brief for the next run
```

---

## Per-Unit Tools

A specialist can add its own deterministic tools in a `tools.py` file inside its folder.

### File structure

```
divisions/
  postgres/
    unit.md
    tools.py              ← optional deterministic tools for this specialist
    requirements.txt      ← optional packages tools.py needs
    agent.py
    api.py
    cli.py
```

### tools.py interface

```python
from typing import Any

class PostgresTools:
    @staticmethod
    def check_migrations(view: "ProjectView", files: list[str]) -> list["Problem"]:
        """Return migration problems specific to Postgres."""
        return [...]

    @staticmethod
    def fix_connection_strings(view: "ProjectView", files: list[str]) -> list["Fix"]:
        """Fix Postgres-specific connection string issues."""
        return [...]
```

**Naming conventions:**
- Functions: `check_<category>`, `fix_<category>`, `generate_<thing>`.
- Return `Problem` or `Fix` objects from `core.models`.

### How per-unit tools are loaded

```python
# In specialist.py or units.py, after importing the unit:
if hasattr(unit.agent, 'tools'):
    custom_checks = unit.agent.tools.check_migrations(view, files)
    custom_fixes = unit.agent.tools.fix_connection_strings(view, files)
```

**Current usage:** None yet; the template is ready when you need it.

---

## Adding a New Tool

### For core tools (affects all units):

1. **Pick a category** (or create a new module):
   - `projectfacts.py` for facts extraction
   - `checks.py` for verification
   - `autofix.py` for fixes
   - Or create `core/my_checks.py` for a new area

2. **Write functions** that take a `ProjectView` (and optionally changed paths):
   ```python
   def my_check(view: ProjectView, changed: list[str]) -> list[Problem]:
       """Check for my specific problem."""
       return [Problem(severity='hard', file='...', issue='...', suggestion='...')]
   ```

3. **Call from `checks.project_problems()` or `autofix.deterministic_fixes()`**:
   ```python
   # In checks.py:
   def project_problems(view, changed=None, *, python_paths=None):
       problems = [
           *undefined_names_problems(...),
           *my_check(view, changed),  # ← add your check
       ]
   ```

4. **Add tests** to `tests/test_tools.py`:
   ```python
   def test_my_check():
       view = ProjectView.of(changes)
       problems = my_check(view, ['file.py'])
       assert len(problems) == 1
   ```

5. **Document** in this file (TOOLS.md) under "Core Tools".

### For per-unit tools (one specialist):

1. **Create `tools.py`** in the specialist's folder.
2. **Add a class** with static methods:
   ```python
   class MySpecialistTools:
       @staticmethod
       def check_x(view, files):
           return [...]
   ```
3. **Add `requirements.txt`** if the tools need packages.
4. **Call in `agent.py`**:
   ```python
   class MySpecialist(UnitAgent):
       tools = MySpecialistTools
   ```

---

## Tool Categories

| Category | Soft/Hard | File | Purpose |
|---|---|---|---|
| Undefined names | Hard | checks.py | Used but not defined, used before definition |
| Root imports | Hard | checks.py | Imports through the project root (`from backend.x`) |
| Python dependencies | Hard | checks.py | Packages imported but not in requirements.txt |
| JS dependencies | Hard | checks.py | Packages imported but not in package.json |
| HTML refs | Hard | checks.py | index.html references files that don't exist |
| Dockerfile | Soft | checks.py | COPY sources exist, build outputs correct, scripts exist |
| API contract | Soft | checks.py | Frontend calls match backend routes/methods |
| Dropped code | Soft | verification.py | Public functions/classes/exports removed |

(Soft = warns after repairs; Hard = blocks if not fixed)

---

## Example: Adding a New Lint Check

Suppose you want to catch unused Django model fields:

1. **Add to `checks.py`:**
   ```python
   def unused_model_fields(view: ProjectView, changed: list[str]) -> list[Problem]:
       """Find Django model fields that are never used."""
       problems = []
       for file_path in changed:
           if 'models.py' not in file_path:
               continue
           source = view.read(file_path)
           # Parse and check...
           problems.append(Problem(
               severity='info',
               file=file_path,
               issue=f'Field {field_name} is never used',
               suggestion=f'Remove it or use it in a migration/query'
           ))
       return problems
   ```

2. **Call it in `project_problems()`:**
   ```python
   def project_problems(view, changed=None, *, python_paths=None):
       # ... existing checks ...
       problems.extend(unused_model_fields(view, changed or []))
   ```

3. **Add a test** in `tests/test_tools.py`:
   ```python
   def test_unused_model_fields():
       changes = ChangeSet.from_files(workspace, [
           ('backend/models.py', 'class User(Model):\n    name = ...\n    old_field = ...')
       ])
       view = ProjectView.of(changes)
       problems = unused_model_fields(view, ['backend/models.py'])
       assert any(p.issue.startswith('Field old_field') for p in problems)
   ```

---

## Limitations and Future Directions

**What the tools don't do (yet):**
- Call Python functions to introspect models, run type checkers, or build the project.
- Extract semantics from type hints (e.g., require `List[int]` on a function parameter).
- Validate SQL queries or database schemas.
- Check performance or security patterns.

**Where to extend:**
- **Model-callable tools:** For checks that need runtime introspection (Django ORM, type hints, etc.).
- **npm run build check:** Sandbox build runs before the test run.
- **Custom checks per unit:** Add `tools.py` to a specialist folder and register in `agent.py`.
