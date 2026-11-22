from coding_agent.models import (
    ChangeType,
    CommandResult,
    ExecutionResult,
    FileChange,
    TaskStatus,
)
from coding_agent.repair_intelligence import (
    FailureCategory,
    FailureDiagnoser,
    FailurePhase,
    RepairStrategyGenerator,
)


def failed_result(*, errors=(), commands=(), changes=()):
    return ExecutionResult(
        task_id="repair-test",
        status=TaskStatus.FAILED,
        errors=tuple(errors),
        command_results=tuple(commands),
        proposed_changes=tuple(changes),
    )


def test_diagnoses_assertion_failure_from_observed_command_output():
    result = failed_result(
        commands=(
            CommandResult(
                command=("python", "-m", "pytest"),
                exit_code=1,
                stderr="AssertionError: expected 2 but received 1",
            ),
        )
    )

    diagnosis = FailureDiagnoser().diagnose(result, "verification")

    assert diagnosis.category == FailureCategory.ASSERTION_FAILURE
    assert diagnosis.phase == FailurePhase.VERIFICATION
    assert diagnosis.actionable is True
    assert diagnosis.confidence == "medium"
    assert any("AssertionError" in item for item in diagnosis.evidence)


def test_diagnoses_syntax_failure():
    result = failed_result(
        commands=(
            CommandResult(
                command=("python", "-m", "compileall"),
                exit_code=1,
                stderr="SyntaxError: invalid syntax at app.py:12",
            ),
        )
    )

    diagnosis = FailureDiagnoser().diagnose(result, "verification")

    assert diagnosis.category == FailureCategory.SYNTAX_FAILURE
    assert diagnosis.actionable is True
    assert diagnosis.confidence == "high"


def test_environment_failure_is_not_marked_actionable():
    result = failed_result(
        commands=(
            CommandResult(
                command=("npm", "test"),
                exit_code=1,
                stderr="'npm' is not recognized as an internal or external command",
            ),
        )
    )

    diagnosis = FailureDiagnoser().diagnose(result, "verification")

    assert diagnosis.category == FailureCategory.ENVIRONMENT_FAILURE
    assert diagnosis.actionable is False


def test_command_timeout_is_environment_failure_even_with_other_diagnostics():
    result = failed_result(
        commands=(
            CommandResult(
                command=("pytest",),
                exit_code=None,
                stderr="AssertionError: expected true",
                timed_out=True,
            ),
        )
    )

    diagnosis = FailureDiagnoser().diagnose(result, "verification")

    assert diagnosis.category == FailureCategory.ENVIRONMENT_FAILURE
    assert diagnosis.actionable is False


def test_planning_failure_can_be_actionable():
    result = failed_result(errors=("Context preparation or planning failed: malformed JSON",))

    diagnosis = FailureDiagnoser().diagnose(result, "planning")

    assert diagnosis.category == FailureCategory.PLANNING_FAILURE
    assert diagnosis.actionable is True


def test_unavailable_model_is_not_retried_as_a_code_repair():
    result = failed_result(errors=("Context preparation or planning failed: connection refused by Ollama",))

    diagnosis = FailureDiagnoser().diagnose(result, "planning")

    assert diagnosis.category == FailureCategory.ENVIRONMENT_FAILURE
    assert diagnosis.actionable is False


def test_repair_strategy_targets_previous_changes_and_preserves_tests():
    result = failed_result(
        commands=(
            CommandResult(
                command=("pytest",),
                exit_code=1,
                stderr="AssertionError: expected 2",
            ),
        ),
        changes=(
            FileChange("src/service.py", ChangeType.MODIFY, "Update service", "VALUE = 2"),
        ),
    )
    diagnosis = FailureDiagnoser().diagnose(result, "verification")
    strategy = RepairStrategyGenerator().generate(diagnosis, result)

    assert strategy.affected_paths == ("src/service.py",)
    assert any("assertion" in item.lower() for item in strategy.instructions)
    assert any("tests" in item.lower() for item in strategy.instructions)
    assert any("human approval" in item.lower() for item in strategy.constraints)


def test_unknown_failure_is_not_automatically_actionable():
    result = failed_result(errors=("Unexpected internal state",))

    diagnosis = FailureDiagnoser().diagnose(result, "execution")

    assert diagnosis.category == FailureCategory.UNKNOWN_FAILURE
    assert diagnosis.actionable is False
    assert diagnosis.confidence == "low"
