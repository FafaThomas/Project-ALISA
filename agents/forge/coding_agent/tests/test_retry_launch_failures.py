from types import SimpleNamespace

from coding_agent.models import CommandResult
from coding_agent.retry import FailureClassifier, RetryPolicy


def _result(*command_results, errors=()):
    return SimpleNamespace(
        status=SimpleNamespace(value="failed"),
        command_results=tuple(command_results),
        errors=tuple(errors),
    )


def test_command_that_could_not_be_launched_triggers_a_verification_retry():
    result = _result(
        CommandResult(command=("flake8", "game.py"), exit_code=None,
                      stderr="Command could not be started: 'flake8' is not installed or is not on PATH."),
        errors=("Required criterion VER-002: FAIL",),
    )

    assert FailureClassifier.classify(result, RetryPolicy()) == "verification"


def test_timed_out_command_is_not_retried():
    result = _result(
        CommandResult(command=("python", "-m", "pytest"), exit_code=None, timed_out=True),
    )

    assert FailureClassifier.classify(result, RetryPolicy()) is None


def test_failed_command_with_exit_code_is_retried_as_before():
    result = _result(CommandResult(command=("python", "-m", "pytest"), exit_code=1))

    assert FailureClassifier.classify(result, RetryPolicy()) == "verification"
