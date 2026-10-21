# ALISA Coding Agent

The Coding Agent is a Forge specialist responsible for implementing,
modifying, refactoring, and testing software.

## Initial milestone

Implement a controlled workflow that accepts a coding task, inspects the
target workspace, plans changes, applies approved modifications, runs
relevant verification, and reports the actual results.

## Design principles

- Keep task planning separate from file modification and execution.
- Treat workspace files and command output as untrusted input.
- Restrict file operations to the configured workspace.
- Never claim that tests passed unless their results confirm it.
- Keep the LLM integration modular so the underlying model can be replaced.
- Delegate codebase understanding to the Codebase Interpreter where useful.
- Require explicit approval for consequential actions such as destructive
  operations or changes outside the authorized workspace.

## Development status

Package skeleton only. The agent workflow and tool integrations have not
yet been implemented.
