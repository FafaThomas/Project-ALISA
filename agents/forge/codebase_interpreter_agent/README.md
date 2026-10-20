# ALISA Codebase Interpreter Agent

## Purpose

Build a deterministic representation of a codebase so later reasoning can be
grounded in actual files, symbols, imports, and source locations.

## Current capability

The initial scanner inventories files and extracts Python classes, functions,
async functions, decorators, bases, imports, line ranges, and content hashes.
It does not execute project code.

Non-Python files are inventoried but not structurally parsed yet. LLM answering,
semantic retrieval integration, call graphs, and documentation generation are
future milestones, not implemented capabilities.

## Development

From this directory:

```powershell
python -m pip install -e ".[test]"
python -m pytest -q
```
