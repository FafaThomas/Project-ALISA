# ALISA Forge Coding Agent

The coding agent is one of The Forge's agents. It is a tree of **units**:

```
Coding Agent (agent)                          lead: Engineering Director
└── 7 divisions                               each with its own lead
    └── 51 specialists
        └── sub-specialists (optional, any depth): a specialist can become a team
```

Every unit, at every level, can be:

- **used on its own** without going through the agent, from Python, the CLI or HTTP;
- **served as its own HTTP service** on its own port;
- **given its own model** in its `unit.md`.

## Folder layout

```
src/coding_agent/
  unit.md                                    the agent: lead, attachments, optional model
  divisions/
    development/
      unit.md                                the division: name, lead, mission, default members
      agent.py  api.py  cli.py               every unit folder has its own entry files (see below)
      python_developer/
        unit.md                              a specialist: kind, responsibility, triggers, prompt
        agent.py  api.py  cli.py             this specialist as an object, a service, a command
      database_developer/
        unit.md                              a specialist ...
        postgres_developer/unit.md           ... that can later become a team of sub-specialists
        qdrant_developer/unit.md
    security/
      dependency_auditor/
        unit.md
        tools.py                             optional deterministic tools
        requirements.txt                     optional packages this unit's tools need
  core/                                      shared runtime used by every unit
  agent.py  api.py  cli.py
```

Every folder with a `unit.md` is a unit; nothing else needs registering.

## unit.md

```markdown
---
id: postgres_developer
name: Postgres Developer
kind: builder
responsibility: PostgreSQL schemas, queries and migrations
triggers: ["postgres", "postgresql", "psql"]
model: qwen2.5-coder:14b
---
# Postgres Developer

## Focus
- Parameterized queries only
- Migrations that can be rolled back
```

| Key | Used by | Meaning |
|---|---|---|
| `id`, `name` | all | `id` must equal the folder name and be unique in the tree |
| `kind` | specialists | `analyst`, `planner`, `designer`, `coordinator`, `builder`, `reviewer`, `repairer`; decides the phase and the `.env` model tier |
| `responsibility` | specialists | one line; shown to leads and the Director |
| `triggers` | all | words that make the unit relevant (whole words; a few stems such as `integrat` match as prefixes) |
| `explicit_only` | specialists | joins a team only when the request names it or its triggers |
| `lead` | agent, divisions, optional for teams | who splits work among the units directly under it |
| `default_members` | divisions | used when the division is called and nothing matches |
| `order` | all | sort order among siblings |
| `attachments` | agent | lend a specialist to another division (Debugging Specialist works inside Development) |
| `model` | all | model for this unit and every unit under it |
| `endpoint` | all | URL of this unit's own service; parents then call it over HTTP |

The Markdown body is the unit's brief:
- **For a specialist**, it's added to the system prompt.
- **For a division**, it's the mission given to the specialists working in it.
- **For a team**, the team's responsibility is passed to its sub-specialists.

Every file is validated on load. A load error names the file. These problems
stop loading:

- a mismatched or duplicate id
- a missing `kind`
- a `lead` that isn't directly under its unit
- a division subfolder without a `unit.md`
- an invalid `endpoint`

## Models

Models are set in the ALISA `.env`, which is organized as faction > agent >
division. A specialist can also set its own `model:` in its `unit.md`.

```
OLLAMA_HOST, OLLAMA_MODEL                    shared by all factions; OLLAMA_MODEL is the last resort
FORGE_CODING_AGENT_PLANNING_MODEL            role-type default: Director, analysts, planners, designers, leads
FORGE_CODING_AGENT_CODING_MODEL              role-type default: developers, builders, Debugging Specialist
FORGE_CODING_AGENT_REVIEW_MODEL              role-type default: reviewers (blank: planning model)
FORGE_CODING_AGENT_MODEL                     the whole agent
FORGE_CODING_AGENT_<DIVISION>_MODEL          one division, e.g. FORGE_CODING_AGENT_DEVELOPMENT_MODEL
FORGE_CODING_AGENT_<DIVISION>_<UNIT>_MODEL   one specialist, e.g. ..._DEVELOPMENT_PYTHON_DEVELOPER_MODEL
```

For each unit, the lookup starts at the unit and walks up: the unit itself,
its team, its division, then the agent. At each level the `.env` variable is
checked first, then `model:` in that unit's `unit.md`, and the first one set
wins. If none is set, the unit's role-type default applies, then
`OLLAMA_MODEL`. Blank values count as unset.

A division model applies to every unit in it, the division lead included. For
example, setting `FORGE_CODING_AGENT_DEVELOPMENT_MODEL` also moves the Tech Lead
to that model. The older names (`PLANNING_OLLAMA_MODEL`,
`CODING_AGENT_OLLAMA_MODEL`, `REVIEW_OLLAMA_MODEL`) are still read as
role-type fallbacks.

Every output in a report records the model used and where the choice came
from. For example:

```
qwen3:8b            .env FORGE_CODING_AGENT_PLANNING_MODEL
qwen2.5-coder:14b   .env FORGE_CODING_AGENT_DEVELOPMENT_MODEL
qwen2.5-coder:14b   unit.md (development/python_developer) via http://127.0.0.1:54510
```

Each output also records the tokens it used (`prompt`, `output`, `thinking`
where reported, and `total`).

`coding-agent units --json` shows each unit's `.env` variable name
(`model_env_var`) and its effective model.

### Gemini (optional cloud models)

Any model setting can point at Google's Gemini API instead of Ollama by adding
a `gemini:` prefix:

```ini
GEMINI_API_KEY=<your AI Studio key>
FORGE_CODING_AGENT_DEVELOPMENT_TECH_LEAD_MODEL=gemini:gemini-3.5-flash-lite
```

A value without a prefix (or with `ollama:`) is an Ollama model, so local stays
the default and only units you assign go to the cloud. Anything a Gemini unit
sees, including project code, leaves the machine. On the free tier, Google may
use it to improve its products.

**Named models** let many units share one Gemini model that you change in one
place:

```ini
FORGE_GEMINI_REASONING_MODEL=gemini-4-hydrogen
FORGE_GEMINI_GENERAL_MODEL=gemini-3.5-flash-lite
FORGE_GEMINI_LITE_MODEL=gemma-4-26b-a4b-it

FORGE_CODING_AGENT_LEADERSHIP_ENGINEERING_DIRECTOR_MODEL=gemini:@reasoning
FORGE_CODING_AGENT_ARCHITECTURE_MODEL=gemini:@general
```

`gemini:@<name>` reads `FORGE_GEMINI_<NAME>_MODEL`. The report shows both steps,
for example `.env FORGE_CODING_AGENT_ARCHITECTURE_MODEL -> .env FORGE_GEMINI_GENERAL_MODEL`.

**Quotas and fallback.** Free tiers allow few requests, so each Gemini call
goes through a quota tracker:

```ini
# requests per minute, tokens per minute, requests per day (0 or blank = no local limit)
GEMINI_QUOTA_GEMINI_4_HYDROGEN=5,250000,20
GEMINI_QUOTA_GEMINI_3_5_FLASH_LITE=15,250000,500
```

- **Over a per-day limit:** the call is not sent.
- **Per-minute window full:** the tracker waits for a slot if one frees up
  within `GEMINI_MAX_WAIT` seconds (default 15); otherwise the call is not sent.
- **Google returns 429:** the model is benched for the delay Google asks for,
  or until the next reset when the daily quota is spent. Calls during the
  bench are not sent.
- **Overload (HTTP 5xx) or a timeout:** the call is retried `GEMINI_RETRIES`
  times (default 2), with short pauses.

When a call is not sent or still fails, that one call goes to a fallback:

1. `FORGE_GEMINI_FALLBACK_MODEL`, when it's set to a different hosted model
   (for example `@general`, so `gemini-2.5-flash` out of quota tries
   `gemini-3.5-flash-lite` next).
2. Otherwise, or if that fails too, the unit's role-type default on Ollama.

Each step is tried once, so it can't loop. The output's model source then says why, e.g.
`gemini:gemini-4-hydrogen unavailable, fell back to the planning default (...)`.

Per-day counts and benches are saved in `~/.alisa/forge/gemini_usage.json`
(set `GEMINI_USAGE_FILE` to move it), so they carry over between runs. They
reset at midnight Pacific, when Google resets its quotas.

Gemini decides its own thinking, so the `think` setting doesn't apply to it.
Streaming, tool calling and multi-turn conversations aren't used, because the
agent makes single structured calls.

**Choosing assignments.** Units that make few calls per run suit the free
tier: the Director, architects and division leads. Developers and the
Debugging Specialist make a call per file plus repairs, so they would use up a
20-requests-per-day model in two or three runs.

### Thinking and temperature

Two more settings use the same lookup as models: the unit's `.env` variable
first, then its `unit.md`, walking up to the agent.

| Setting | `.env` example | `unit.md` | Default |
|---|---|---|---|
| Thinking | `FORGE_CODING_AGENT_ARCHITECTURE_THINK=true` | `think: true` | off |
| Temperature | `FORGE_CODING_AGENT_DEVELOPMENT_TEMPERATURE=0` | `temperature: 0.2` | `CODING_AGENT_TEMPERATURE`, else 0.2 |

**Thinking** is off by default. Thinking models such as qwen3 and deepseek-r1
otherwise reason before answering. In benchmarks on an 11 GB card, this made
calls 3–5x slower and sometimes used up the whole output budget before any
JSON was written. Turn it on only for units where the extra reasoning is worth
the wait. **Temperature** defaults low, so that runs are more repeatable.

## Using one unit on its own

```python
from coding_agent import CodingRequest, InvocationContext, InvocationSource, load_unit

request = CodingRequest(task="Add an index on users.email", workspace=r"D:\work\app")
ui = InvocationContext(source=InvocationSource.UI)

load_unit("development/database_developer/postgres_developer").run(request, ui)  # by path
load_unit("dependency_auditor").run(request, ui)                                 # or by id
load_unit("security").run(request, ui)                                           # a division
load_unit().run(request, ui)                                                     # the whole agent
```

```powershell
coding-agent run D:\work\app "Add an index on users.email" --unit postgres_developer
```

What each kind of unit does when called on its own:

| Unit | Behaviour |
|---|---|
| A specialist | works alone, with no lead and no Director |
| A team specialist | routes the work to its members (see below) |
| A division | picks its members from its triggers without a model call, falling back to `default_members` |
| The Engineering Director | returns the team it would pick |
| The Tech Lead | returns a plan |

## Each unit's own entry files

Every unit folder (divisions, specialists, sub-specialists) has three small
files bound to that unit:

| File | What it does |
|---|---|
| `agent.py` | the unit as a Python object (a `UnitAgent` subclass), and the place for this unit's own behaviour |
| `api.py` | `python api.py --allow-root D:/work --port 8801`: serve this unit as its own HTTP service |
| `cli.py` | `python cli.py D:/work/app "<task>"`: run this unit on one task (same options as `coding-agent run`) |

The agent's own folder is the `coding_agent` package itself. Its `agent.py`,
`api.py` and `cli.py` are the shared implementation, and you run it with
`python -m coding_agent` or `coding-agent`.

The entry files find their unit from their own location, so they can't point
at the wrong one. All shared logic stays in `core/`, so a fix there applies to
every unit at once. `coding-agent scaffold` creates the three files for new
unit folders, and `coding-agent scaffold --check` reports any that are missing.

**Custom behaviour per unit**

Override hooks in the unit's `agent.py`:

```python
from coding_agent.unit_entry import UnitAgent


class PostgresDeveloper(UnitAgent):
    unit_file = __file__

    def extra_context(self, query, workspace_root):
        """Text added to this unit's context before its model is called."""
        return "Target PostgreSQL 16. Use migrations under db/migrations."

    def review(self, files):
        """Deterministic findings over (path, content) pairs."""
        return [{"severity": "high", "file": p, "issue": "DROP TABLE without IF EXISTS"}
                for p, c in files if "DROP TABLE " in c and "IF EXISTS" not in c]

    def after_output(self, output, action):
        """Inspect or adjust this unit's output (consult, coordinate, build, repair, review)."""
        return output
```

From Python, `load_unit_agent("postgres_developer")` returns that class,
ready to `run(...)`. If an `agent.py` is broken, it is logged and ignored, and
the unit falls back to the shared behaviour. `tools.py` files still work.

## Teams: when a specialist becomes several

Add sub-specialist folders inside a specialist. From outside, nothing changes:
the Director and Tech Lead still see and assign "Database Developer". Inside,
the team specialist handles work in one of three ways:

- **One member's triggers match** (for example "postgres"): that member does the work, with no extra model call.
- **Several members match:** the team specialist acts as their lead and splits the work.
- **No member matches:** the team specialist does the work itself, as the generalist, using its own `unit.md`.

Sub-specialists know their division and their team. Both appear in their
prompt, and they can still be called directly by path.

## Units as separate services

```powershell
# the whole agent
coding-agent serve --allow-root D:\work --port 8765

# one unit on its own port
coding-agent serve --allow-root D:\work --unit development/database_developer --port 8801
```

| Method | Path | |
|---|---|---|
| GET | `/health` | liveness, no auth |
| GET | `/units` | the served unit and everything under it |
| POST | `/runs` | run the served unit (`?wait=true` to block) |
| POST | `/units/<path>/runs` | run a unit under the served one |
| GET | `/runs/<job_id>` | status, progress events, report |
| POST | `/invoke` | internal: a parent unit delegating one step to this unit |

To have parents call a served unit instead of running it in-process, give the
unit an `endpoint:` in its `unit.md`. The parent then sends that unit each step
it should do, together with the request, the shared notes and the proposed
changes so far. The unit returns its output. Everything the unit proposes is
re-checked by the parent's workspace guard before it's accepted.

**Rules for served units:**
- **The unit you called always runs locally.** A service never calls itself,
  and the caller's own process makes the final write.
- **Only the unit you called writes to disk.** Remote units only propose.
- **Workspace access:** served units need access to the same workspace paths,
  under `--allow-root`.
- **Authentication:** `CODING_AGENT_API_TOKEN` protects every endpoint except
  `/health`, and parents send the same token when calling remote units.
- **Requirements:** on start, `serve` warns about any `requirements.txt`
  package missing from the served unit's environment.
- **No Forge RAG across services:** the Forge's retriever can't be sent over
  HTTP, so a remote unit runs on Markdown context.
- **If a service is unreachable** it is reported as an error in that unit's
  output; the run itself does not crash.

## How a run works

The unit you call opens the run and owns it. Phases run in this order:

```
analyze -> plan -> design -> build -> files gate + repair -> review -> (optional fix round)
```

- **Agent:** the Engineering Director picks a proportionate team. A keyword pass comes first, then
  optional model refinement, validated against the caps:
  - 1 analyst, 2 planners, 2 designers
  - 3 developers, 2 support builders, 2 reviewers
  - at most 10 specialists in total

  Divisions then run in order, phase by phase.
- **Division leads** split build work into items, one owner per file, when the division has two or
  more builders. Development's Tech Lead does this whenever Development builds.
- **One owner per file, enforced.** Each file a lead assigns belongs to that work item. A specialist
  writing a file owned by another work item is rejected, and the rejection is listed in the run's
  warnings. New files nobody was assigned are open, a team specialist's sub-specialists may write
  their parent item's files, and the Debugging Specialist's repairs may change any file.
- **Developers see each other's work.** Each developer gets the signatures of the Python files
  teammates already wrote this run. Leads must name, in each work item, the functions and classes a
  file has to provide.
- **Target platform.** Every specialist is told the OS and Python version the code must run on:
  this machine by default, `CODING_AGENT_TARGET_ENVIRONMENT` in `.env`, or `target_environment`
  on the request.
- **Leads plan only their division's part.** Each division lead is told which builders from other
  divisions are on the run, and leaves their work to them. For example, the Tech Lead leaves
  Dockerfiles to DevOps.
  - An **empty plan** while other divisions are building means "this is their part": the division
    builds nothing. (Treating it as a failure once put the Database Developer on a Dockerfile.)
  - A lead whose model **fails to plan** gets one retry. If it fails again, the run stops and writes
    nothing, instead of letting every specialist guess at its part.
- **Builders check their own files.** Right after a builder writes its files, the tools add imports
  that have one right answer, then check the files for syntax errors, undefined names and imports of
  names that don't exist. The builder gets one round to fix what's left (`CODING_AGENT_SELF_CHECK`,
  default 1, `0` turns it off) before the next work item starts.
- **Builders get facts, not guesses.** Each builder and repair gets a "Project facts" section read
  from the code for the files it works on: backend routes with their methods (for frontend and
  Docker files), the frontend's API calls (for backend files), the dependencies already listed, the
  project layout, and known problems in those files.
- **The tools fix what has one right answer** before the gate runs, and again before every repair
  round. See [The team's tools](#the-teams-tools).
- **Existing code is kept.** Builders that modify a file must keep the functions, classes, exports and
  URL routes already in it.
- **Big work items are written file by file.** Leads give each work item at most 3 files.
  - A work item with 3 or more files is written one model call per file.
  - So is any work item whose single answer ran out of output tokens.
  - Repairs touching 3 or more files, or cut off the same way, go one file at a time too.
- **Specialists with nothing to do may say so.** A specialist without a lead-assigned work item is
  told which files other work items already own, and that an empty answer is fine. If it still
  tries to change owned files, the rejections show up as one warning, not one per file.
- **Backup folders are ignored.** Folders named `*.bak`, `*.old`, `*.orig`, `*.backup`, `backup`,
  `backups` or `old` are not read as project files, nor copied into the test run.
- **The files gate** is the only blocking check. It requires that:
  - every assigned file is produced
  - Python compiles
  - JSON parses
  - **Python imports work:**
    - every `from x import name` between the project's own modules finds `name`
    - imported project modules exist
    - imports are resolved from parent folders as well, as when a Django project runs from `backend/`
    - files from earlier runs that import a changed module are checked too
  - **Django string references point at real modules:**
    - `include("app.urls")`
    - `ROOT_URLCONF`, `WSGI_APPLICATION` and `ASGI_APPLICATION`
    - `INSTALLED_APPS` and `MIDDLEWARE`
    - middleware classes listed in `INSTALLED_APPS` are reported
    - folders of modules without `__init__.py` count as project packages
    - a Django settings module with no `manage.py` in the project is reported
  - **JavaScript/TypeScript relative imports work:**
    - the imported file exists
    - default, named and `ns.member` imports match what that file exports
  - **Names are defined** (changed Python files):
    - every name used is defined or imported, following Python's scoping rules; the problem says
      which import fits when exactly one does
    - nothing is used at import time before its definition, such as a class body naming a class
      defined further down
    - imports don't go through the folder the code runs from (`from backend.menu.models` in a
      project whose `manage.py` is in `backend/`)
  - **Dependencies are listed:**
    - packages the Python code imports, the Django settings load (`INSTALLED_APPS`, `MIDDLEWARE`,
      backends, the database driver), or a Dockerfile or script starts (`gunicorn`) are in the
      nearest `requirements.txt` or `pyproject.toml`
    - packages the frontend imports are in its `package.json`, and a frontend without one is reported
    - a Vite app's `package.json` has a `build` script
  - **`index.html` loads files that exist.**
  - **Dockerfiles fit the project** (soft: warns after repairs):
    - `COPY` sources exist; a missing file is reported under its own path, so it gets created
    - a stage copies the build from where the build tool writes it (Vite: `dist/`)
    - `npm ci` has a lock file; `npm run <script>` scripts exist
    - `python manage.py ...` and `gunicorn module:app` in the CMD, entrypoint scripts and supervisord
      programs find their files in the image, given `WORKDIR`, `cd` and `--chdir`
  - **The frontend calls routes the backend has** (soft): every API call found in the frontend
    (axios, instances with a `baseURL`, `fetch`) is matched against the Django routes, including DRF
    routers, `@action` routes and the methods each view allows. An SPA catch-all route doesn't count.

  Problems in files the run didn't touch are not this run's job: they are listed as open issues in
  the project brief instead.

  If the gate fails, the Debugging Specialist gets up to 2 repair rounds. If it still fails, nothing is
  written, and proposals are kept under `.coding-agent/runs/<run_id>/proposed/`.
- **Dropped code** is reported as a soft gate problem: a change that removes public functions,
  classes, JS exports or URL routes from an existing file. The Debugging Specialist gets it first,
  together with the file's version from before the run, so it can restore the code. If the code is
  still missing after repairs, it becomes a warning, since the task may really have asked for the
  removal.
- **The Debugging Specialist's test run** (Python projects).
  - **What it runs:** once the gate passes, the Debugging Specialist plans up to 8 commands that
    exercise what the request asked for, such as `python main.py add 12.50 food`, then `list`.
  - **Where:** they run in a throwaway copy of the project, never in the workspace.
  - **Dependencies:** when the project has `requirements.txt` files, their packages are installed
    into a throwaway virtual environment. The environment is cached by package list under
    `~/.alisa/forge/testrun-envs` (`CODING_AGENT_TEST_ENV_DIR`).
    - Only plain `name[extras] <version>` lines are installed, as prebuilt wheels
      (`--only-binary=:all:`), so no package build scripts run.
    - URLs, local paths, `-e`, `-r` and pip options are refused.
    - Set `CODING_AGENT_TEST_DEPS=off` to skip installing.
  - **Subfolders:** `python manage.py check` finds `backend/manage.py` and runs it from `backend/`.
  - **Settings:** values from the project's `.env.example` (root, or one folder down) are set in the
    sandbox, so code that requires them can start. Only placeholders: real `.env` files are never read.
  - **Migrations:** before the commands run, Django's own `makemigrations` generates the migrations
    for the project's apps in the sandbox. New migration files join the proposal, marked as made by
    the tools.
  - **Import check:** the harness, not a model, then imports every project module in the sandbox.
    For Django that's every module under the folder `manage.py` runs from, after `django.setup()`,
    followed by the system checks with every module loaded. For other projects, only modules inside
    packages are imported: a top-level script would start its program. It catches code that fails
    as soon as it is loaded, such as `from django.setup import setup`, a name a package doesn't
    have, or a test module that defines a clashing model. A failure involving a file the run changed
    goes to repair, and follows the test run's mode. A failure in code the run left alone is a
    warning.
  - **Modules are not run as scripts:** a planned `python backend/orders/serializers.py` is refused.
    A Django module run directly fails before its own code runs ("settings are not configured"),
    and so does a module with relative imports. In a real run, seven such commands sent the Debugging
    Specialist to rewrite six files that had no bug.
  - **Not run at all** is a warning, never silent: the test run turned off, no commands planned, or
    every planned command refused.
  - **Servers:** commands that serve until stopped (`manage.py runserver`, `shell` and similar) are
    skipped. A command that times out after printing that it started serving ("Starting development
    server", "Listening on" and similar) counts as "needs a server", not a hang. On a timeout, the
    command and every process it started are stopped, including Django's autoreload child.
  - **Failures:** these go back for repair, like a gate failure:
    - a command that hangs (`CODING_AGENT_TEST_TIMEOUT`, default 20 s)
    - a command that ends in a Python traceback
    - a non-zero exit with an import error, even when printed without a traceback
    - Django system-check errors (pointed at the file Django names)
    - `makemigrations --check` finding missing migrations
    - a package the code imports that is missing from `requirements.txt`, also when the program
      prints its own "could not find X ... pip install x" message instead of a traceback
    - a pinned version pip can't find while other versions exist, such as `psycopg[binary]==3.1.10`
      on Python 3.12. The pin goes to repair before any command runs. A package with no prebuilt
      build at all, or a network error, is only a warning.
    - `manage.py <subcommand>` printing Django's command list instead of running the subcommand
      (a `manage.py` that passes `sys.argv[1:]`), or Django reporting an unknown command

    A program that exits with its own error message is not a failure. A command that fails only
    because a database or other service isn't running is shown as `n/a`.
  - **Safety limits:**
    - Only `python <project file>` and `python -m <project module>` commands run, with no shell.
    - Commands with `<placeholders>` are skipped.
    - stdin is closed.
    - No API keys or tokens are passed in.
    - Home and temp folders point inside the copy, which is deleted afterwards.
  - **After repairs:** failures that are still there become warnings and the files are still
    written, because a planned command can itself be wrong. Set `CODING_AGENT_TEST_RUN=block` to
    refuse writing instead, or `off` to skip the test run.
  - **In the report:** `report.json` keeps every command's exit code and output tails.
  - **What it doesn't replace:** this makes the agent execute the code it generated. It catches
    hangs and crashes; whether the program does the right thing is still up to your own testing.
- **Review findings** are advisory. `--fix-review` sends high and critical findings back for one round.
- **Failures are visible.**
  - A run where every specialist failed, and nothing was delivered, reports `failed` with the reasons.
  - When some specialists fail, the run continues, and each failure is listed as a warning.
- **The CLI** proposes first and writes only after you type `yes`.

## The team's tools

Small models write plausible files but lose track of how files fit together. The tools take the
parts with one right answer off the model, and give it facts instead of making it guess. None of
them calls a model, and every fix is part of the proposal you approve, marked `(tools)`.

| Tool | What it does |
|---|---|
| Facts (`core/projectfacts.py`) | reads layout, dependencies, Django routes and allowed methods, frontend API calls and environment variables from the code |
| Checks (`core/checks.py`) | the gate checks above, for one run or the whole project |
| Fixes (`core/autofix.py`) | adds missing packages to `requirements.txt` (known PyPI names only); creates or completes `package.json` with versions known to work together and the Vite scripts; creates a missing `manage.py`, `wsgi.py` or `asgi.py` from the settings; fixes a `manage.py` that passes `sys.argv[1:]`; adds imports that have one right answer and strips the run folder from imports (`backend.menu.models` -> `menu.models`); in Dockerfiles, `npm ci` without a lock file becomes `npm install` and a Vite build is copied from `dist/` |
| Migrations (`testrun.generate_migrations`) | Django's `makemigrations` for the project's apps, in the sandbox |
| Brief and changelog (`core/projectdocs.py`) | what one run leaves for the next |

Everything else (route design, which side of an API mismatch is wrong, where a command should run)
stays a problem for the model, stated exactly.

**What one run leaves for the next.** After a run writes changes:

- `CHANGELOG.md` in the project gets an entry, newest first: the task, files created, changed and
  deleted, what the gate and the test run found, and what was left open. It comes from the run
  report, not from a model.
- `.coding-agent/PROJECT.md` is rewritten: requirements from earlier runs, layout, dependencies,
  backend routes, frontend API calls, environment variables, open issues found in the code, and
  what the run left open.
- **Requirements carry over.** Every run's `--constraint` and `--acceptance-criteria` values are
  added to the brief's "Requirements from earlier runs" (up to 40; one given again moves up with the
  newer run id). The changelog entry lists them too. A later "fix whatever is wrong" run then still
  knows that, say, the API must stay under `/api/`.

At the start of the next run, the brief is rebuilt from the code as it is then (so your own edits
count), plus the requirements, what the last run left open and the recent changelog entries.
Analysts, planners, designers and leads get the whole brief; leads are told to plan fixes for open
issues that block the request or sit in files they plan to change. Builders and the Debugging
Specialist get the requirements and the facts about their own files, and are told to fix the side of
a frontend/backend mismatch that breaks a requirement.

Requirements are recorded from the first run with this version on. For rules that should hold from
the start, or that earlier runs were given, write them in `.coding-agent/memory/CONVENTIONS.md`:
every specialist reads it.

## Context

- **API, UI and CLI** use Markdown context, in priority order:
  1. `.coding-agent/memory/CONVENTIONS.md`
  2. the project brief (analysts, planners, designers and leads; see above)
  3. `PROJECT_MEMORY.md`, holding the last 15 run summaries
  4. project docs
  5. a file map of the workspace
  6. the assigned files
  7. ranked source excerpts
- **The Forge** (`CodingAgent.forge_invocation(retriever)`) adds retrieved Forge knowledge, which
  every in-process unit under the one called inherits.

## Command line

```powershell
python -m pip install -e ".[test]"

coding-agent units                                     # the tree, with leads, models, endpoints, tools
coding-agent units --unit development --json
coding-agent run D:\work\app "<task>"                  # whole agent
coding-agent run D:\work\app "<task>" --unit security  # any unit by path or id
coding-agent team D:\work\app "<task>"                 # preview the agent's team (no model)
coding-agent check D:\work\app [--facts]               # open issues read from the code (no model, writes nothing)
coding-agent fix D:\work\app [--yes]                   # apply the tools' fixes to the whole project, after approval
coding-agent serve --allow-root D:\work --unit postgres_developer --port 8801
```

`run` options:
- `--propose-only` never writes.
- `--role <id>` forces a specialist onto the team.
- `--max-team N` caps the team size.
- `--fix-review` sends high and critical findings back for one fix round.
- `--no-director` uses the keyword team only.
- `--max-repairs N` sets the number of repair rounds.
- `--json` prints the report as JSON.

## Configuration

| Variable | Purpose |
|---|---|
| `OLLAMA_HOST` | Ollama URL (default `http://localhost:11434`) |
| `OLLAMA_MODEL`, `FORGE_CODING_AGENT_*_MODEL` | models per role type, agent, division or specialist (see Models) |
| `FORGE_CODING_AGENT_*_THINK`, `FORGE_CODING_AGENT_*_TEMPERATURE`, `CODING_AGENT_TEMPERATURE` | thinking and temperature per unit (see Thinking and temperature) |
| `CODING_AGENT_LLM_TIMEOUT`, `CODING_AGENT_OLLAMA_NUM_PREDICT`, `CODING_AGENT_OLLAMA_NUM_CTX` | Ollama request limits |
| `CODING_AGENT_TARGET_ENVIRONMENT` | OS and Python the generated code must run on (default: this machine) |
| `CODING_AGENT_TEST_RUN`, `CODING_AGENT_TEST_TIMEOUT` | Debugging Specialist test run: `repair` (default), `block` or `off`; seconds per command (default 20) |
| `CODING_AGENT_SELF_CHECK` | rounds a builder gets to fix problems in its own files right after writing them (default 1, `0` = off) |
| `CODING_AGENT_TEST_DEPS`, `CODING_AGENT_TEST_ENV_DIR`, `CODING_AGENT_TEST_INSTALL_TIMEOUT` | Install `requirements.txt` for the test run: `install` (default) or `off`; cache folder (default `~/.alisa/forge/testrun-envs`); seconds per install (default 600) |
| `GEMINI_API_KEY`, `GEMINI_API_BASE` | Gemini access (only needed when a unit uses `gemini:`) |
| `FORGE_GEMINI_<NAME>_MODEL` | named Gemini models, used as `gemini:@<name>` |
| `GEMINI_QUOTA_<MODEL>` | `rpm,tpm,rpd` limits for quota-aware routing |
| `GEMINI_TIMEOUT`, `GEMINI_RETRIES`, `GEMINI_MAX_WAIT` | Gemini request timeout (default 120 s), retries on overload (default 2), longest wait for a per-minute slot (default 15 s) |
| `GEMINI_USAGE_FILE` | where per-day Gemini usage is saved (default `~/.alisa/forge/gemini_usage.json`) |
| `CODING_AGENT_API_TOKEN` | bearer token for services and for calls to remote units |
| `CODING_AGENT_REMOTE_TIMEOUT` | seconds to wait for a remote unit (default 3600) |

## Run artifacts

Each run writes to `<workspace>/.coding-agent/runs/<run_id>/`:

- `report.json` (each output's model, model source and token usage, plus the run's warnings)
- `handoff.md`
- `changes.jsonl` (when changes are written)
- `backup/` (copies of overwritten files)
- `proposed/` (when nothing was written)

Runs that write changes also update `<workspace>/CHANGELOG.md` and `<workspace>/.coding-agent/PROJECT.md`
(see [The team's tools](#the-teams-tools)). `coding-agent fix` writes to `runs/<time>-tools/`.

## Tests

```powershell
python -m pytest -q
```

The tests use a scripted fake model that answers per unit, and run real local
HTTP services for the remote-unit tests. No Ollama is needed.
