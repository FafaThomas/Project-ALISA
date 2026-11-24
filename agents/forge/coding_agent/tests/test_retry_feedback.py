from coding_agent.models import (
    ChangeType,
    CommandResult,
    ExecutionResult,
    FileChange,
    TaskStatus,
)
from coding_agent.retry import RetryRequest, build_recovery_context, failing_check_digest

# Shape of the real failure from the Snake benchmark run.
PYTEST_OUTPUT = """FF                                                                       [100%]
================================== FAILURES ===================================
_______________________________ test_start_game _______________________________

    @pytest.mark.req_006
    def test_start_game():
        game_state = start_game()
>       assert isinstance(game_state, GameState)
E       assert False
E        +  where False = isinstance(None, GameState)

tests\\test_req_006.py:7: AssertionError
___________________________ test_update_game_state ____________________________

    @pytest.mark.req_006
    def test_update_game_state():
>       new_state = update_game_state(start_game(), "UP")
E       NameError: name 'update_game_state' is not defined

tests\\test_req_006.py:14: NameError
=========================== short test summary info ===========================
FAILED tests/test_req_006.py::test_start_game - assert False
FAILED tests/test_req_006.py::test_update_game_state - NameError: name 'updat...
2 failed in 0.10s
"""


def _result(*, file_size=0):
    changes = tuple(
        FileChange(f"module_{index}.py", ChangeType.CREATE, "Create module", "x = 1\n" * file_size)
        for index in range(6)
    )
    return ExecutionResult(
        task_id="task-1",
        status=TaskStatus.FAILED,
        command_results=(
            CommandResult(command=("python", "-c", "print('ok')"), exit_code=0, stdout="Verified 6 changed path(s)"),
            CommandResult(command=("python", "-m", "pytest", "-q"), exit_code=1, stdout=PYTEST_OUTPUT),
        ),
        errors=("Verification command exited with code 1.",),
        proposed_changes=changes,
    )


def _request(result):
    return RetryRequest(
        task_id="task-1", attempt_number=1, max_attempts=3, phase="verification",
        summary="Implementation verification failed.", errors=result.errors, result=result,
    )


def test_digest_keeps_the_lines_that_explain_each_failure():
    text = "\n".join(failing_check_digest(_result()))

    assert "FAILED CHECK: python -m pytest -q (exit code 1)" in text
    assert "E        +  where False = isinstance(None, GameState)" in text
    assert "E       NameError: name 'update_game_state' is not defined" in text
    assert "tests\\test_req_006.py:14: NameError" in text
    assert "FAILED tests/test_req_006.py::test_start_game - assert False" in text
    # Passing checks are left out.
    assert "Verified 6 changed path(s)" not in text


def test_failing_output_survives_truncation_when_previous_files_are_large():
    # Six files of ~6000 chars each exceed the 24000-char cap on their own.
    context = build_recovery_context(_request(_result(file_size=1500)))

    assert len(context) <= 24000
    assert "NameError: name 'update_game_state' is not defined" in context
    assert context.index("FAILING CHECKS FROM THE PREVIOUS ATTEMPT") < context.index(
        "Previous proposed changes (already rolled back):"
    )


def test_digest_falls_back_to_the_end_of_unrecognized_output():
    result = ExecutionResult(
        task_id="task-1",
        status=TaskStatus.FAILED,
        command_results=(CommandResult(
            command=("node", "check.js"), exit_code=2,
            stdout="starting\n" * 500 + "check failed: 3 problems found",
        ),),
    )

    text = "\n".join(failing_check_digest(result))

    assert "check failed: 3 problems found" in text
    assert len(text) < 3000


def test_untagged_requirement_failure_explains_which_marker_is_missing():
    result = ExecutionResult(
        task_id="task-1",
        status=TaskStatus.FAILED,
        command_results=(CommandResult(
            command=("python", "-m", "pytest", "-q", "-p", "no:cacheprovider", "-m", "req_002", "tests/test_game.py"),
            exit_code=5,
            stdout="2 deselected in 0.01s\n",
        ),),
    )

    text = "\n".join(failing_check_digest(result))

    assert "No test was collected for marker expression 'req_002'" in text
    assert "@pytest.mark.req_002" in text


def test_repeated_failures_across_checks_are_shown_once():
    # The benchmark ran the same failing tests under several commands.
    same = CommandResult(command=("python", "-m", "pytest", "-q"), exit_code=1, stdout=PYTEST_OUTPUT)
    again = CommandResult(command=("python", "-m", "pytest", "tests/test_req_006.py"), exit_code=1, stdout=PYTEST_OUTPUT)
    result = ExecutionResult(task_id="task-1", status=TaskStatus.FAILED, command_results=(same, again))

    digest = "\n".join(failing_check_digest(result))
    context = build_recovery_context(_request(result))

    assert digest.count("NameError: name 'update_game_state' is not defined") == 1
    assert "FAILED CHECK: python -m pytest tests/test_req_006.py (exit code 1)" in digest
    assert "(same failures as shown for an earlier check above)" in digest
    assert "(output repeats failures already shown for an earlier check)" in context
    # Only the first check's raw output is repeated in full.
    assert context.count("2 failed in 0.10s") == 1
