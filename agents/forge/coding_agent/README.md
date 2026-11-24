# ALISA Forge Coding Agent

The coding agent is one of The Forge's agents. Inside it are seven divisions
holding 51 specialists. Every level can be called on its own:

| Level | What it is | Lead | How to call it |
|---|---|---|---|
| Faction | The Forge | (Forge) | wraps this agent; not part of this package |
| Agent | the coding agent | Engineering Director | `agent.run(...)` |
| Division | e.g. Security | from `division.md` | `agent.division("security").run(...)` |
| Specialist | e.g. Dependency Auditor | none | `agent.specialist("dependency_auditor").run(...)` |

Every unit has the same entry point and returns the same report:
`run(request, invocation, emit=None) -> RunReport`. The report records which
unit was called (`unit_level`, `unit_id`).

## Folder layout

```
src/coding_agent/
  agent.md                      agent brief: lead, attachments
  agent.py                      CodingAgent, DivisionUnit, SpecialistUnit
  api.py  cli.py                HTTP API and command line
  core/                         shared runtime used by every unit
    registry.py                 loads the folders below and validates them
    units.py                    session, division runner, phases, files gate
    selection.py                team selection (agent and division level)
    specialist.py  prompts.py   running one specialist
    blackboard.py               shared handoff log
    context/                    Markdown and Forge RAG context
    llm.py  workspace.py  verification.py  models.py
  divisions/
    <division>/division.md                       name, lead, order, default members, mission
    <division>/<specialist>/specialist.md        kind, responsibility, triggers + prompt body
    <division>/<specialist>/tools.py             optional deterministic tools
```

The seven divisions and their leads:

| Division | Lead |
|---|---|
| leadership | Engineering Director (also the agent's lead) |
| architecture | Software Architect |
| development | Tech Lead |
| quality | QA Lead |
| security | Security Architect |
| devops | DevOps Engineer |
| documentation | Technical Writer |

Run `coding-agent units` to print the whole tree.

### Adding or changing a specialist

Create `divisions/<division>/<specialist_id>/specialist.md`. Nothing else
needs editing.

```markdown
---
id: terraform_engineer
name: Terraform Engineer
kind: builder
responsibility: Terraform modules and state management
triggers: ["terraform", ".tf", "hcl"]
---
# Terraform Engineer

## Focus
- One module per concern, with inputs and outputs documented
- Never commit state files
```

- **`kind`** is one of `analyst`, `planner`, `designer`, `coordinator`,
  `builder`, `reviewer` or `repairer`. It decides the specialist's phase and
  model tier.
- **`triggers`** are matched as whole words against requests. A few word stems
  (such as `integrat`) match as prefixes.
- **`explicit_only: true`** keeps a specialist off a team unless the request
  names it or its triggers.
- **The Markdown body** becomes part of the specialist's system prompt.

Every file is validated at startup. A malformed file, an id that doesn't match
its folder, a lead outside its own division, a duplicate id, or a folder
without `specialist.md` stops loading with an error naming the file.

**Optional `tools.py`** gives a specialist deterministic behaviour of its own:

- `extra_context(query, workspace_root) -> str | None` is added to the
  specialist's context.
- `review(files: list[(path, content)]) -> list[dict]` adds findings, even
  when the model is unavailable.

Two specialists have tools today: the Code Security Auditor (risky patterns
such as `eval`, `shell=True`, hard-coded credentials, SQL built from strings)
and the Dependency Auditor (unpinned requirements and package.json versions,
plus manifests added as context).

## How the units work together

- **Requests go down, reports come up.** A unit hands its members the request
  plus the shared blackboard, which holds everything decided so far.
- **Only the unit you called writes to disk.** That unit runs the files gate
  and repairs, then applies the changes or keeps them as a proposal. Units
  below it only add proposed changes.
- **Context is inherited.** Forge RAG applies only when the Forge called; every
  other caller gets Markdown context. A division or specialist run inside a
  Forge call inherits RAG.
- **Leads assign work only when there is work to split.** A division lead
  splits the build into work items, one owner per file, when the division has
  two or more builders. Development's Tech Lead does this whenever
  Development builds. Otherwise each builder takes its part directly, which
  saves a model call. A specialist called on its own always works alone.
- **Attachments** (in `agent.md`) lend a specialist to another division at the
  agent level. The Debugging Specialist (Quality) works inside Development, so
  the Tech Lead can assign it bug-fix work.

### Agent run

1. The Engineering Director picks a proportionate team. A keyword pass comes
   first, then optional refinement by the planning model, validated against
   the caps:
   - 1 analyst, 2 planners, 2 designers
   - 3 developers, 2 support builders, 2 reviewers
   - at most 10 roles in total

   Division leads join when their division has work to split.
2. Phases run in order: `analyze → plan → design → build → files gate/repair →
   review → (optional fix round)`. Within each phase, divisions run in their
   `order`, which also groups model calls by tier. On one GPU the planning and
   coding models therefore swap only a few times per run.

### Division run

The division chooses its own members from its triggers, without a model call.
If nothing matches, it falls back to `default_members` (Development falls
back to the workspace's language). It runs the same phases. A review-only
division, such as Security, reviews the existing workspace files most related
to the request.

### Specialist run

The specialist works alone:
- A builder writes the files for the whole request.
- A reviewer reviews the related workspace files.
- The Tech Lead alone returns a plan.
- The Engineering Director alone returns the team it would pick.

### Files gate

This is the only blocking check: every assigned file was produced, Python
compiles and JSON parses. Generated code is never executed; functional
testing stays with you. When the gate fails:
- The Debugging Specialist gets up to 2 repair rounds.
- If the gate still fails, nothing is written, and the proposals are kept
  under `.coding-agent/runs/<run_id>/proposed/`.

Review findings are advisory.

## Context management

**Markdown (API, UI, CLI)**

Sources are listed in priority order and trimmed to a per-specialist budget:

1. `.coding-agent/memory/CONVENTIONS.md`: your project rules.
2. `.coding-agent/memory/PROJECT_MEMORY.md`: a summary of each completed run,
   keeping the last 15.
3. Project docs.
4. A file map of the workspace.
5. The assigned files, as the team currently sees them.
6. Source excerpts ranked by overlap with the task.

**Forge RAG (only when the Forge invokes)**

Same as Markdown, except item 6 comes from the Forge collections. If
retrieval fails, it falls back to lexical ranking.

```python
from coding_agent import CodingAgent, CodingRequest
from knowledge_ingestion_service.retrieval import FactionRetriever

agent = CodingAgent()
forge = CodingAgent.forge_invocation(FactionRetriever(), factions=("forge",))
agent.run(CodingRequest(task="Add CSV export", workspace=r"D:\work\reports"), forge)
agent.division("security").run(CodingRequest(task="Audit the code", workspace=r"D:\work\reports"), forge)
```

## Command line

```powershell
python -m pip install -e ".[test]"

coding-agent units                                                     # the hierarchy
coding-agent run D:\work\calc "Build a calculator CLI in Python"       # whole agent
coding-agent run D:\work\calc "Audit the code and its dependencies" --division security
coding-agent run D:\work\calc "Write a README" --specialist technical_writer
coding-agent team D:\work\calc "Add unit tests" --division quality     # preview members, no model
```

`run` proposes changes, prints them, and writes only after you type `yes`.

Other options:
- `--propose-only` never writes.
- `--role <id>` forces a specialist onto the team.
- `--max-team N` caps the team size.
- `--fix-review` sends high/critical findings back for one fix round.
- `--no-director` uses the keyword team only.
- `--json` prints the report as JSON.

## HTTP API

```powershell
coding-agent serve --allow-root D:\work --port 8765
# optional: $env:CODING_AGENT_API_TOKEN = "..."   (then send Authorization: Bearer ...)
```

| Method | Path | |
|---|---|---|
| GET | `/health` | liveness, no auth |
| GET | `/units` | the agent > division > specialist tree |
| POST | `/runs` | run the whole agent |
| POST | `/divisions/<id>/runs` | run one division |
| POST | `/specialists/<id>/runs` | run one specialist |
| GET | `/runs/<job_id>` | status, progress events, report |

POST bodies take these fields: `task`, `workspace`, `constraints`,
`acceptance_criteria`, `mode` (`apply` or `propose`), `requested_roles`,
`max_team_size`, `fix_review_findings`. A POST returns 202 with a job id; add
`?wait=true` to block until the run finishes. Workspaces must be under an
`--allow-root`. The server binds to `127.0.0.1` by default.

## Configuration

Values are read from the environment or the ALISA project `.env`.

| Variable | Purpose |
|---|---|
| `OLLAMA_HOST` | Ollama URL (default `http://localhost:11434`) |
| `PLANNING_OLLAMA_MODEL` | planning tier: Director, analysts, planners, designers, division leads |
| `CODING_AGENT_OLLAMA_MODEL` | coding tier: builders and the Debugging Specialist |
| `REVIEW_OLLAMA_MODEL` | reviewers (falls back to the planning model) |
| `OLLAMA_MODEL` | fallback for every tier |
| `CODING_AGENT_LLM_TIMEOUT`, `CODING_AGENT_OLLAMA_NUM_PREDICT`, `CODING_AGENT_OLLAMA_NUM_CTX` | request limits |

## Run artifacts

Each run writes to `<workspace>/.coding-agent/runs/<run_id>/`:

- `report.json`
- `handoff.md`
- `changes.jsonl` (when changes are written)
- `backup/` (copies of overwritten files)
- `proposed/` (when nothing was written)

## Tests

```powershell
python -m pytest -q
```

The tests use a scripted fake LLM that answers per specialist. No Ollama is
needed.
