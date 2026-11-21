
import pytest

from coding_agent.models import CommandResult
from coding_agent.verification import (
    VerificationEngine,
    VerificationError,
)


@pytest.fixture
def engine():
    return VerificationEngine()


def make_result(
    command,
    *,
    exit_code=0,
    stdout="",
    stderr="",
    timed_out=False,
):
    return CommandResult(
        command=tuple(command),
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        timed_out=timed_out,
    )


def test_all_required_commands_pass(engine):
    required = [
        ("python", "-m", "pytest"),
        ("python", "-m", "compileall", "src"),
    ]

    results = [
        make_result(required[0]),
        make_result(required[1]),
    ]

    report = engine.evaluate(required, results)

    assert report.passed is True
    assert len(report.successful_results) == 2
    assert report.failed_results == ()
    assert report.issues == ()
    assert "2 of 2 checks succeeded" in report.summary


def test_nonzero_exit_code_fails_verification(engine):
    required = [("python", "-m", "pytest")]
    results = [
        make_result(
            required[0],
            exit_code=1,
            stdout="2 failed",
        )
    ]

    report = engine.evaluate(required, results)

    assert report.passed is False
    assert len(report.failed_results) == 1
    assert any(
        issue.code == "command_failed"
        for issue in report.issues
    )


def test_timeout_fails_verification(engine):
    required = [("python", "-m", "pytest")]
    results = [
        make_result(
            required[0],
            exit_code=None,
            timed_out=True,
        )
    ]

    report = engine.evaluate(required, results)

    assert report.passed is False
    assert any(
        "timed out" in issue.message
        for issue in report.issues
    )


def test_missing_result_fails_verification(engine):
    required = [
        ("python", "-m", "pytest"),
        ("python", "-m", "compileall", "src"),
    ]

    results = [make_result(required[0])]

    report = engine.evaluate(required, results)

    assert report.passed is False
    assert any(
        issue.code == "missing_result"
        for issue in report.issues
    )


def test_extra_result_fails_verification(engine):
    required = [("python", "-m", "pytest")]
    results = [
        make_result(required[0]),
        make_result(("python", "--version")),
    ]

    report = engine.evaluate(required, results)

    assert report.passed is False
    assert any(
        issue.code == "unexpected_result"
        for issue in report.issues
    )


def test_command_mismatch_fails_verification(engine):
    required = [("python", "-m", "pytest")]
    results = [
        make_result(("python", "--version"))
    ]

    report = engine.evaluate(required, results)

    assert report.passed is False
    assert any(
        issue.code == "command_mismatch"
        for issue in report.issues
    )


def test_command_order_must_match(engine):
    required = [
        ("python", "-m", "pytest"),
        ("python", "--version"),
    ]

    results = [
        make_result(required[1]),
        make_result(required[0]),
    ]

    report = engine.evaluate(required, results)

    assert report.passed is False
    assert sum(
        issue.code == "command_mismatch"
        for issue in report.issues
    ) == 2


def test_empty_required_commands_fail_closed(engine):
    report = engine.evaluate([], [])

    assert report.passed is False
    assert len(report.issues) == 1
    assert report.issues[0].code == "no_required_commands"


def test_multiple_failed_commands_are_reported(engine):
    required = [
        ("python", "-m", "pytest"),
        ("python", "-m", "compileall", "src"),
    ]

    results = [
        make_result(required[0], exit_code=1),
        make_result(required[1], exit_code=2),
    ]

    report = engine.evaluate(required, results)

    assert report.passed is False
    assert len(report.failed_results) == 2
    assert sum(
        issue.code == "command_failed"
        for issue in report.issues
    ) == 2


@pytest.mark.parametrize(
    "commands",
    [
        "",
        "python -m pytest",
        None,
        [()],
        [("python", "")],
        [("python", "   ")],
    ],
)
def test_invalid_required_commands_are_rejected(engine, commands):
    with pytest.raises(VerificationError):
        engine.evaluate(commands, [])


@pytest.mark.parametrize(
    "results",
    [
        "",
        None,
        ["not a CommandResult"],
    ],
)
def test_invalid_results_are_rejected(engine, results):
    with pytest.raises(VerificationError):
        engine.evaluate(
            [("python", "-m", "pytest")],
            results,
        )


def test_report_preserves_all_execution_results(engine):
    required = [
        ("python", "-m", "pytest"),
        ("python", "--version"),
    ]

    results = [
        make_result(required[0], stdout="Tests passed"),
        make_result(required[1], stdout="Python 3.12"),
    ]

    report = engine.evaluate(required, results)

    assert report.results == tuple(results)
    assert report.required_commands == tuple(required)
