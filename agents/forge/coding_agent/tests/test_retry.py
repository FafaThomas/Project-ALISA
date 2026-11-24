from dataclasses import replace

from coding_agent.models import (
    ChangeType,
    CommandResult,
    ExecutionResult,
    FileChange,
    TaskStatus,
)
from coding_agent.retry import (
    FailureClassifier,
    RetryPolicy,
    RetryProgressTracker,
    RetryRequest,
    build_recovery_context,
)


def failed_result(*, errors=(), changes=(), commands=(), report=None):
    return ExecutionResult(
        task_id="task-1",
        status=TaskStatus.FAILED,
        errors=tuple(errors),
        proposed_changes=tuple(changes),
        command_results=tuple(commands),
        plan_summary="Implement the requested feature",
        specification_report=report,
    )


def test_classifier_recognizes_espec_and_structured_planning_failures():
    policy = RetryPolicy()
    for message in (
        "ESpec plan validation failed after bounded corrections",
        "Missing implementation coverage for REQ-005",
        "Ollama returned malformed JSON",
        "Planner could not produce a valid implementation plan",
    ):
        result = failed_result(errors=(message,))
        assert FailureClassifier.classify(result, policy) == "planning"


def test_classifier_does_not_retry_unknown_or_environment_failures():
    policy = RetryPolicy()
    assert FailureClassifier.classify(
        failed_result(errors=("Unexpected internal error",)), policy
    ) is None

    timeout = failed_result(commands=(
        CommandResult(("pytest",), None, timed_out=True),
    ))
    assert FailureClassifier.classify(timeout, policy) is None


def test_recovery_context_preserves_original_scope_and_criterion_state():
    report = {
        "acceptance_criteria": [
            {
                "criterion_id": "REQ-1-AC-1",
                "description": "Create the frontend directory",
                "status": "PASS",
                "reason": "Directory exists",
            },
            {
                "criterion_id": "REQ-2-AC-1",
                "description": "Configure database healthcheck",
                "status": "FAIL",
                "reason": "Healthcheck is missing",
            },
        ],
        "criteria": [],
    }
    result = failed_result(
        errors=("Healthcheck is missing",),
        report=report,
    )
    request = RetryRequest(
        task_id="task-1",
        attempt_number=1,
        max_attempts=3,
        phase="verification",
        summary="Acceptance verification failed",
        errors=result.errors,
        result=result,
        original_task_description="Build the burger ordering application",
        original_constraints=("Use PostgreSQL", "Do not add unrelated services"),
        original_acceptance_criteria=(
            "Create frontend directory",
            "Configure database healthcheck",
        ),
    )

    context = build_recovery_context(request)

    assert "Build the burger ordering application" in context
    assert "Use PostgreSQL" in context
    assert "Do not add unrelated services" in context
    assert "Create frontend directory" in context
    assert "REQ-1-AC-1 [PASS]" in context
    assert "REQ-2-AC-1 [FAIL]" in context
    assert "A previous PASS is historical evidence only" in context
    assert "preserve previously passing behavior" in context.casefold()


def test_progress_tracker_stops_exact_repetition_but_allows_changed_plan():
    first = failed_result(
        errors=("Assertion failed",),
        changes=(FileChange("app.py", ChangeType.CREATE, "Create app", "VALUE = 1\n"),),
        commands=(CommandResult(("pytest",), 1, stderr="assertion failed"),),
    )
    tracker = RetryProgressTracker()

    initial = tracker.observe(first)
    repeated = tracker.observe(first)
    changed = tracker.observe(replace(
        first,
        proposed_changes=(
            FileChange("app.py", ChangeType.CREATE, "Create app", "VALUE = 2\n"),
        ),
    ))

    assert not initial.repeated
    assert repeated.repeated
    assert repeated.occurrence == 2
    assert not changed.repeated
    assert changed.fingerprint != repeated.fingerprint
