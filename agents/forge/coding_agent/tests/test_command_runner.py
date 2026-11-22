
import sys

import pytest

from coding_agent.command_runner import (
    CommandAuthorizationError,
    CommandRunner,
    CommandValidationError,
)
from coding_agent.models import CommandResult
from coding_agent.workspace import Workspace


def make_runner(tmp_path, **kwargs):
    return CommandRunner(
        workspace=Workspace(tmp_path),
        **kwargs,
    )


def test_executes_approved_command_inside_workspace(tmp_path):
    runner = make_runner(tmp_path)

    command = (
        sys.executable,
        "-c",
        "import os; print(os.getcwd())",
    )

    result = runner.run(
        command,
        approved_commands=[command],
    )

    assert isinstance(result, CommandResult)
    assert result.succeeded
    assert result.stdout.strip() == str(tmp_path)


def test_captures_stdout_and_stderr(tmp_path):
    runner = make_runner(tmp_path)

    command = (
        sys.executable,
        "-c",
        "import sys; print('OUT'); print('ERR', file=sys.stderr)",
    )

    result = runner.run(command, approved_commands=[command])

    assert result.stdout.strip() == "OUT"
    assert result.stderr.strip() == "ERR"
    assert result.succeeded


def test_rejects_unapproved_command(tmp_path):
    runner = make_runner(tmp_path)

    command = (sys.executable, "-c", "print('UNAUTHORIZED')")

    with pytest.raises(CommandAuthorizationError):
        runner.run(command, approved_commands=[])


def test_rejects_command_that_differs_from_approved_command(tmp_path):
    runner = make_runner(tmp_path)

    approved = (sys.executable, "-c", "print('SAFE')")
    requested = (sys.executable, "-c", "print('DIFFERENT')")

    with pytest.raises(CommandAuthorizationError):
        runner.run(requested, approved_commands=[approved])


@pytest.mark.parametrize(
    "command",
    [
        (),
        ("",),
        (sys.executable, ""),
        "python -m pytest",
        None,
    ],
)
def test_rejects_malformed_commands(tmp_path, command):
    runner = make_runner(tmp_path)

    with pytest.raises(CommandValidationError):
        runner.run(command, approved_commands=[command])


def test_returns_nonzero_exit_code(tmp_path):
    runner = make_runner(tmp_path)

    command = (sys.executable, "-c", "raise SystemExit(7)")

    result = runner.run(command, approved_commands=[command])

    assert result.exit_code == 7
    assert not result.succeeded
    assert not result.timed_out


def test_timeout_is_reported(tmp_path):
    runner = make_runner(tmp_path, timeout=0.2)

    command = (
        sys.executable,
        "-c",
        "import time; time.sleep(5)",
    )

    result = runner.run(command, approved_commands=[command])

    assert result.timed_out
    assert result.exit_code is None
    assert not result.succeeded


def test_output_is_truncated(tmp_path):
    runner = make_runner(tmp_path, max_output_chars=20)

    command = (
        sys.executable,
        "-c",
        "print('A' * 1000)",
    )

    result = runner.run(command, approved_commands=[command])

    assert result.succeeded
    assert len(result.stdout) <= 20


def test_missing_executable_is_reported(tmp_path):
    runner = make_runner(tmp_path)

    command = ("definitely-not-a-real-executable-731",)

    result = runner.run(command, approved_commands=[command])

    assert result.exit_code is None
    assert not result.succeeded
    assert result.stderr


def test_python_commands_do_not_create_workspace_bytecode_cache(tmp_path):
    runner = make_runner(tmp_path)
    (tmp_path / "sample_module.py").write_text("VALUE = 42\n", encoding="utf-8")
    command = (
        sys.executable,
        "-c",
        "import sample_module; print(sample_module.VALUE)",
    )

    result = runner.run(command, approved_commands=[command])

    assert result.succeeded
    assert result.stdout.strip() == "42"
    assert not (tmp_path / "__pycache__").exists()


def test_malformed_sibling_approval_does_not_block_exact_valid_approval(tmp_path):
    runner = make_runner(tmp_path)
    command = (sys.executable, "-c", "print('verified')")
    approved = [
        ("python", " ", "backend/manage.py", "check"),
        command,
    ]

    result = runner.run(command, approved_commands=approved)

    assert result.succeeded
    assert result.stdout.strip() == "verified"
