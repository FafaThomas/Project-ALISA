# ALISA Coding Agent

The Coding Agent is a Forge specialist for controlled software implementation, modification, refactoring, and verification.

## Current capabilities

- Human-approved implementation plans and verification commands.
- Workspace-scoped file access, guarded writes, recovery records, and rollback.
- Polyglot source indexing through a shared parser contract and explicit parser registry.
- Language-specific extraction for Python, JavaScript/TypeScript/JSX/TSX, SQL, Go, Rust, C/C++, C#, Java, and additional configuration, markup, and scripting formats.
- Typed relationships with resolved, unresolved, ambiguous, and external states.
- Language-specific dependency resolvers currently covering Python, JavaScript/TypeScript family, Go, and Rust.
- Parser diagnostics with source coordinates for syntax failures.
- Queryable in-memory knowledge graph with module/symbol nodes, typed relationship edges, bounded traversal, and impact analysis.
- Context discovery uses graph relationships across indexed languages to retrieve relevant dependencies and dependents.

## Architecture

`CodeIndexer` discovers supported files, selects a parser per file, and creates language-neutral module, symbol, import, and reference records. `RelationshipEngine` then delegates language-specific dependency interpretation to `ImportResolverRegistry`. Parsing and dependency resolution remain separate operations. `CodeIndex.build_knowledge_graph()` converts the resulting index into an immutable graph without reparsing files. The graph supports node search, incoming/outgoing edge queries, bounded breadth-first traversal, and resolved upstream impact analysis. Unresolved and ambiguous references remain visible as reference nodes rather than being silently discarded. Context discovery uses the graph to expand task-relevant files across languages.

Parser extraction depth varies by language. A registered parser indicates supported grammar/format handling, not complete compiler-equivalent semantic analysis. Unsupported extensions are ignored; syntax failures are retained on the corresponding module without aborting the workspace index.

## Tests

Run the full suite from the project root:

```powershell
python -m pytest -v
```

Representative parser fixtures and golden expectations live under `tests/parsers/fixtures` and `tests/parsers/golden`.

## Indexing benchmark

Run the lightweight benchmark against a representative workspace:

```powershell
python scripts/benchmark_index.py "D:\path\to\workspace" --max-files 2000
```

It reports elapsed indexing time, indexed files per second, peak traced Python memory, language counts, and relationship count. Results are environment-specific and should be compared on the same machine and workspace.

## Command-line interface

After installing the project (`python -m pip install -e ".[test]"`), use the
`coding-agent` command to inspect a workspace. The CLI is read-only in this
milestone: it builds an in-memory index/graph for each invocation and does not
write an index database or modify project files.

```powershell
# Build and summarize a multi-language index
coding-agent index "D:\path\to\workspace"

# Display a bounded graph overview
coding-agent graph "D:\path\to\workspace" --max-nodes 50 --max-edges 100

# Search modules, symbols, and references
coding-agent search "D:\path\to\workspace" UserService --kind symbol

# Inspect incoming/outgoing relationships (use an exact node ID if ambiguous)
coding-agent relationships "D:\path\to\workspace" "symbol:src/app.py::UserService@10:class" --direction both

# Find upstream impact of a symbol
coding-agent impact "D:\path\to\workspace" "symbol:src/app.py::UserService@10:class" --depth 2

# Assemble bounded task context without invoking an LLM or changing files
coding-agent context "D:\path\to\workspace" "Trace user authentication flow"

# Machine-readable output for scripts and integrations
coding-agent index "D:\path\to\workspace" --json
```

Available inspection commands are `index`, `graph`, `search`, `relationships`,
`impact`, and `context`. These commands rebuild the index and graph in memory
for each invocation. The `--json` flag is supported by the inspection commands.

### Approval-gated coding workflow

Pass 2 adds an interactive `run` command that assembles bounded project context,
asks the configured Ollama model for a structured implementation plan, displays
the proposed file diffs and verification commands, and waits for explicit human
approval before applying anything.

```powershell
# Generate and review a plan; type yes only after reviewing the complete proposal
coding-agent run "D:\path\to\workspace" "Add input validation to the user registration endpoint"

# Add explicit constraints and acceptance criteria
coding-agent run "D:\path\to\workspace" "Add input validation" `
  --constraint "Do not change the public API" `
  --acceptance-criteria "Invalid email addresses are rejected"
```

The `run` command uses `OLLAMA_MODEL` and optional `OLLAMA_HOST` environment
settings (or the ALISA project `.env`). It does not accept a non-interactive
approval flag. Only the exact response `yes` authorizes the displayed plan.
Approved file operations are journaled under `.coding-agent/changes.jsonl` by
default. Verification commands are shown before approval and run with the
workspace as their working directory, without a shell. Failed verification
causes the orchestrator to attempt rollback using its recorded recovery data.


### Bounded recovery and verification

The orchestrator supports bounded recovery attempts. The initial implementation
counts as attempt one; the default maximum is three total attempts. After a
recoverable planning or verification failure, the orchestrator has already
rolled back any applied changes before it asks the caller whether another plan
may be generated. A retry is never automatic unless the caller explicitly
approves it through the `retry_handler` callback. Every corrective plan passes
through the normal implementation approval handler again.

```powershell
# Allow up to three total attempts; approve each retry and each corrective plan
coding-agent run "D:\path\to\workspace" "Add input validation"

# Disable retries by limiting the task to its initial attempt
coding-agent run "D:\path\to\workspace" "Add input validation" --max-attempts 1
```

Recoverable planning errors and ordinary non-zero verification results can be
retried. Command timeouts, command-launch failures, workspace/authorization
failures, and write/integrity failures are not automatically retried. Recovery
feedback includes the previous plan, rolled-back proposed changes, and bounded
stdout/stderr diagnostics. Attempt and retry-decision events are persisted in
`.coding-agent/tasks.jsonl`; file transaction events remain in
`.coding-agent/changes.jsonl`.

When acceptance criteria are supplied, the planner is instructed to include
focused behavioral tests where practical. Proposed pytest test files are
considered when selecting verification, even before they exist on disk. These
checks are displayed for approval with the rest of the plan. Python verification
commands set `PYTHONDONTWRITEBYTECODE=1`; pytest is selected with its cache
provider disabled. This reduces common workspace cache artifacts but is not an
OS-level sandbox and does not prevent arbitrary approved commands from writing
other files.

The current retry callback is an orchestration interface used by CLI or future
API adapters; this patch does not add HTTP endpoints, SSE transport, or a
persistent remote task-session service.
