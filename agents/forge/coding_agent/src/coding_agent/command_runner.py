
"""Safely execute explicitly approved commands inside a workspace."""

from __future__ import annotations

import os
import subprocess
import threading
from collections.abc import Sequence
from pathlib import Path

from coding_agent.models import CommandResult
from coding_agent.workspace import Workspace


class CommandRunnerError(RuntimeError):
    """Base exception for command runner errors."""


class CommandValidationError(CommandRunnerError):
    """Raised when a command is malformed."""


class CommandAuthorizationError(CommandRunnerError):
    """Raised when a command has not been explicitly approved."""


class CommandRunner:
    """Execute approved commands with bounded output and time limits."""

    def __init__(
        self,
        workspace: Workspace,
        *,
        timeout: float = 120.0,
        max_output_chars: int = 10_000,
        encoding: str = "utf-8",
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be greater than zero")

        if max_output_chars < 0:
            raise ValueError("max_output_chars must not be negative")

        self.workspace = workspace
        self.timeout = timeout
        self.max_output_chars = max_output_chars
        self.encoding = encoding

    @staticmethod
    def _validate_command(command: Sequence[str]) -> tuple[str, ...]:
        """Validate and normalize a command argument sequence."""

        if isinstance(command, (str, bytes)) or command is None:
            raise CommandValidationError(
                "Command must be a sequence of argument strings, not a string"
            )

        try:
            normalized = tuple(command)
        except TypeError as exc:
            raise CommandValidationError(
                "Command must be an iterable of argument strings"
            ) from exc

        if not normalized:
            raise CommandValidationError("Command must not be empty")

        if any(
            not isinstance(part, str) or not part.strip()
            for part in normalized
        ):
            raise CommandValidationError(
                "Command arguments must be non-empty strings"
            )

        return normalized

    def _validate_approval(
        self,
        command: tuple[str, ...],
        approved_commands: Sequence[Sequence[str]],
    ) -> None:
        """Require an exact match against an approved command."""

        if isinstance(approved_commands, (str, bytes)):
            raise CommandValidationError(
                "Approved commands must be a sequence of command sequences"
            )

        try:
            approved = tuple(
                self._validate_command(item)
                for item in approved_commands
            )
        except TypeError as exc:
            raise CommandValidationError(
                "Approved commands must be iterable"
            ) from exc

        if command not in approved:
            raise CommandAuthorizationError(
                "Command has not been explicitly approved"
            )

    def _resolve_workspace(self) -> Path:
        """Resolve the workspace directory before execution."""

        try:
            root = self.workspace.root.resolve(strict=True)
        except OSError as exc:
            raise CommandRunnerError(
                f"Could not resolve workspace: {exc}"
            ) from exc

        if not root.is_dir():
            raise CommandRunnerError(
                "Workspace root is not a directory"
            )

        return root

    def _capture_output(
        self,
        stream,
        destination: list[str],
        lock: threading.Lock,
    ) -> None:
        """Drain a subprocess pipe while retaining bounded decoded output."""

        remaining = self.max_output_chars

        try:
            while True:
                chunk = stream.read(4096)

                if not chunk:
                    break

                if remaining <= 0:
                    continue

                decoded = chunk.decode(
                    self.encoding,
                    errors="replace",
                )

                retained = decoded[:remaining]

                with lock:
                    destination.append(retained)

                remaining -= len(retained)

        finally:
            stream.close()

    @staticmethod
    def _terminate_process(process: subprocess.Popen) -> None:
        """Terminate a process and wait briefly before forcing termination."""

        if process.poll() is not None:
            return

        try:
            process.terminate()
            process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        except OSError:
            if process.poll() is None:
                process.kill()
                process.wait()

    def run(
        self,
        command: Sequence[str],
        *,
        approved_commands: Sequence[Sequence[str]],
    ) -> CommandResult:
        """Execute one approved command and return its observed result."""

        normalized = self._validate_command(command)

        self._validate_approval(
            normalized,
            approved_commands,
        )

        workspace_root = self._resolve_workspace()

        stdout_parts: list[str] = []
        stderr_parts: list[str] = []
        output_lock = threading.Lock()

        try:
            process = subprocess.Popen(
                normalized,
                cwd=workspace_root,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                text=False,
            )
        except OSError as exc:
            return CommandResult(
                command=normalized,
                exit_code=None,
                stdout="",
                stderr=f"Could not start command: {exc}",
                timed_out=False,
            )

        stdout_thread = threading.Thread(
            target=self._capture_output,
            args=(process.stdout, stdout_parts, output_lock),
            daemon=True,
        )

        stderr_thread = threading.Thread(
            target=self._capture_output,
            args=(process.stderr, stderr_parts, output_lock),
            daemon=True,
        )

        stdout_thread.start()
        stderr_thread.start()

        timed_out = False

        try:
            process.wait(timeout=self.timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            self._terminate_process(process)

        stdout_thread.join()
        stderr_thread.join()

        with output_lock:
            stdout = "".join(stdout_parts)
            stderr = "".join(stderr_parts)

        if timed_out:
            timeout_message = (
                f"Command timed out after {self.timeout} seconds"
            )
            stderr = (
                f"{stderr}\n{timeout_message}".strip()
            )

        return CommandResult(
            command=normalized,
            exit_code=None if timed_out else process.returncode,
            stdout=stdout,
            stderr=stderr,
            timed_out=timed_out,
        )
