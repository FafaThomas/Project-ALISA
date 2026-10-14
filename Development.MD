# ALISA Development Workflow

## 1. Purpose

This document defines the development and version-control workflow for Project ALISA.

The objectives are to:

- Keep `main` as the authoritative, integrated branch.
- Isolate new development from stable code.
- Establish consistent branch naming conventions.
- Make development, testing, and integration predictable.
- Keep GitLab and GitHub synchronized.
- Ensure completed work is integrated before development branches are removed.

---

## 2. Branch Strategy

ALISA uses three primary branch categories.

| Branch | Purpose |
|---|---|
| `main` | Authoritative, integrated project state |
| `feature/*` | New capabilities, agents, services, tools, integrations, and substantial refactors |
| `hotfix/*` | Small, targeted bug fixes |

### 2.1. Main Branch

`main` is the authoritative branch of Project ALISA.

Rules:

- All new development should originate from an updated `main`.
- Features and fixes should be developed on dedicated branches.
- Changes must be validated before integration.
- The working tree should be clean after completing a development task.
- The updated `main` must be pushed to both configured remotes.
- Do not delete `main`.

### 2.2. Feature Branches

**Naming convention:**

`feature/<faction>/<description>`

Feature branches are used for:

- Developing new agents.
- Creating new services.
- Adding tools and integrations.
- Introducing new faction capabilities.
- Implementing significant architectural changes.
- Refactoring existing components.
- Developing shared infrastructure.

Examples:

```text
feature/forge/robotics-agent
feature/forge/knowledge-ingestion-v2
feature/forge/code-interpreter
feature/corpus/language-ingestion
feature/quill/ethics-agent
feature/core/router-refactor
```

Feature branches should represent a cohesive development objective. Avoid combining unrelated features into one branch.

### 2.3. Hotfix Branches

**Naming convention:**

`hotfix/<faction>/<description>`

Hotfix branches are intended for small, targeted bug fixes that do not require a broader feature-development effort.

Examples:

```text
hotfix/forge/utf16-decoding
hotfix/forge/stale-point-cleanup
hotfix/corpus/parser-crash
hotfix/core/routing-error
```

Use a feature branch instead when a supposed hotfix expands into a substantial refactor or new capability.

---

## 3. Faction Naming Convention

The faction segment identifies the primary area of responsibility for the work.

| Faction | Primary Domain |
|---|---|
| `forge` | Engineering, software development, networking, hacking, robotics, and physical engineering |
| `pentagram` | Scientific research, mathematics, physics, chemistry, biology, and medicine |
| `canvas` | Art, writing, music, illustration, videography, and other creative disciplines |
| `quill` | Psychology, ethics, law, sociology, anthropology, philosophy, and other social sciences |
| `gauntlet` | Wealth generation, financial management, and economic strategy |
| `sword` | Defense, martial arts, fitness, weapon mastery, and defensive systems |
| `knife` | Food, cooking, agriculture, hunting, and related disciplines |
| `corpus` | Languages, linguistics, and cultures |
| `core` | Shared infrastructure, orchestration, routing, and cross-faction platform components |

Use lowercase faction names and hyphens between words.

### Cross-Faction Development

A project may involve multiple factions without requiring a separate branch for every faction.

Choose the faction responsible for the primary deliverable. Use `core` when the primary change concerns shared infrastructure or orchestration rather than one faction's capabilities.

For example, a scientific experiment platform might involve:

- `pentagram` for scientific feasibility.
- `quill` for ethical considerations.
- `forge` for implementation and required tooling.

The branch should reflect the principal deliverable, not every discipline involved.

---

## 4. Standard Development Lifecycle

### Step 1: Synchronize with Main

Start from the latest version of `main`.

```powershell
git switch main
git fetch --all --prune
git pull --ff-only origin main
```

The `--ff-only` option prevents Git from silently creating an unexpected merge commit during the update.

### Step 2: Create a Development Branch

For a new feature:

```powershell
git switch -c feature/forge/example-feature
```

For a hotfix:

```powershell
git switch -c hotfix/forge/example-fix
```

Replace the example faction and description with the appropriate values.

### Step 3: Develop

Implement the intended change within the branch.

Guidelines:

- Keep changes relevant to the branch's objective.
- Follow the existing project architecture and conventions.
- Update documentation when behavior, interfaces, configuration, or architecture changes.
- Add or update tests when functionality changes.
- Avoid unrelated refactoring.
- Do not commit API keys, credentials, secrets, local environments, or unnecessary generated artifacts.
- Use descriptive commit messages.

### Step 4: Validate

Before integration, review the working tree and the complete change set.

```powershell
git status
git diff
git diff --check
```

Run the relevant test suite and validation commands for the component being developed.

For example, a Python service may require:

```powershell
python -m pytest
```

Use the project's actual test commands and environment.

Validation should establish that:

- The intended functionality works.
- Relevant tests pass.
- No unintended files or changes are included.
- Documentation reflects the implemented behavior.
- Known limitations are documented rather than misrepresented as resolved.

### Step 5: Integrate into Main

Once the work is validated, synchronize `main` and merge the completed branch.

```powershell
git switch main
git pull --ff-only origin main
git merge --no-ff feature/forge/example-feature
```

Replace the example branch name with the actual branch.

Resolve merge conflicts deliberately. After resolving conflicts, rerun the relevant tests.

For solo development, integration can happen locally. Pull requests or merge requests may be used when code review, collaboration, or CI checks are needed.

### Step 6: Push to Both Remotes

ALISA currently uses two remotes:

- `origin` — GitLab.
- `github` — GitHub.

After integration:

```powershell
git push origin main
git push github main
```

Both remotes must reflect the same integrated commit.

If either push fails, investigate the discrepancy before continuing. Do not assume that a successful push to one remote means the other is synchronized.

### Step 7: Verify the Integrated State

Verify the local branch and both remote references:

```powershell
git log -1 --oneline main
git ls-remote origin refs/heads/main
git ls-remote github refs/heads/main
```

The commit hash reported by both remote `main` references should match the intended local `main` commit.

### Step 8: Delete the Completed Branch

After successful integration and verification, remove the completed branch.

Delete the local branch:

```powershell
git branch -d feature/forge/example-feature
```

Delete the remote branches:

```powershell
git push origin --delete feature/forge/example-feature
git push github --delete feature/forge/example-feature
```

Replace the example name with the actual branch.

Use `git branch -d` rather than `git branch -D` for routine cleanup. The former protects against deleting a branch whose work Git does not recognize as merged.

**Never delete a development branch before confirming that its work has been integrated or that its removal is explicitly intended.**

---

## 5. Commit Conventions

Commit messages should describe the change clearly and concisely.

Recommended prefixes:

| Prefix | Intended Use | Example |
|---|---|---|
| `feat:` | New functionality | `feat: add faction-aware ingestion` |
| `fix:` | Bug fixes | `fix: handle UTF-16 source files` |
| `refactor:` | Structural changes without intended behavior changes | `refactor: simplify ingestion pipeline` |
| `test:` | Test additions or modifications | `test: cover stale point reconciliation` |
| `docs:` | Documentation changes | `docs: establish development workflow` |
| `chore:` | Maintenance and tooling | `chore: update development dependencies` |

These prefixes are conventions rather than a requirement to split every change into multiple commits.

Keep commits logically related, and avoid mixing unrelated features, fixes, and maintenance work.

---

## 6. Testing and Quality Expectations

Testing requirements depend on the component being changed.

At minimum:

1. Run the relevant tests.
2. Review the changed files.
3. Check for unintended changes.
4. Update affected documentation.
5. Verify that the change integrates with the existing architecture.

For changes involving databases, vector stores, external APIs, or other services, distinguish between unit tests and integration tests. A passing unit test does not prove that an external dependency or the complete system works correctly.

If testing cannot be completed, explicitly document what was tested, what was not tested, and why.

---

## 7. Documentation Expectations

Each agent or service should maintain documentation appropriate to its responsibilities.

When introducing or modifying a component, update the relevant documentation to explain:

- Its purpose and responsibilities.
- Its inputs and outputs.
- Its interfaces and dependencies.
- Its configuration requirements.
- How to run or test it.
- Its integration points with other ALISA components.
- Known limitations and outstanding work.

Do not document intended behavior as implemented behavior unless it has actually been implemented and validated.

Project-wide architectural decisions and workflow changes should be recorded in the appropriate central documentation.

---

## 8. Repository and Remote Management

The current remote configuration is:

```text
origin  -> GitLab
github  -> GitHub
```

For this workflow, `main` is the authoritative branch on both platforms.

Rules:

- Keep both remote `main` references synchronized.
- Publish integrated work to both remotes.
- Verify remote state after significant branch operations.
- Investigate failed pushes rather than ignoring them.
- Do not force-push as a routine way to resolve synchronization problems.
- Preserve recovery references before intentional history replacement or destructive branch cleanup.

Remote branches should be removed only when their work is integrated or their deletion is explicitly intended.

---

## 9. Destructive Operations and Recovery

Branch deletion, history rewriting, and replacing shared branch references can result in lost work if performed carelessly.

Before a destructive operation:

1. Inspect the current branch and working tree.
2. Confirm the target commit and affected references.
3. Check both remotes when remote state matters.
4. Create a recovery reference when appropriate.
5. Prefer normal fast-forward pushes whenever possible.
6. Use `--force-with-lease` only when history replacement is intentional and the remote state has been checked.

Do not delete `main`.

Do not delete unmerged branches unless their removal is explicitly intended.

When a branch reset or major architectural cleanup is planned, retain an appropriate recovery reference until the new state has been verified.

---

## 10. Quick Reference

| Task | Branch Pattern |
|---|---|
| New agent | `feature/<faction>/<agent-name>` |
| New service | `feature/<faction>/<service-name>` |
| New tool or integration | `feature/<faction>/<tool-name>` |
| Significant refactor | `feature/<faction>/<refactor-name>` |
| Small bug fix | `hotfix/<faction>/<bug-name>` |
| Shared orchestration | `feature/core/<change-name>` |
| Shared infrastructure fix | `hotfix/core/<bug-name>` |

### Standard Workflow

```text
main
  |
  v
Create feature/ or hotfix/ branch
  |
  v
Implement changes
  |
  v
Test and validate
  |
  v
Merge into main
  |
  v
Push main to GitLab and GitHub
  |
  v
Verify both remote commit hashes
  |
  v
Delete the completed development branch
```

**Default rule:** Every new development effort starts from `main`, happens on a dedicated branch, is tested before integration, and is merged back into `main`. Both remotes are synchronized before the completed branch is deleted.