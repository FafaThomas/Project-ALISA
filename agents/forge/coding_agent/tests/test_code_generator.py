from __future__ import annotations

import pytest

from coding_agent.code_generator import (
    CodeGenerationError,
    UnitCodeGenerator,
)
from coding_agent.engineering_spec import (
    EngineeringSpecification,
    ImplementationUnit,
    Requirement,
    RequirementKind,
)


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts = []

    def generate_json(self, prompt):
        self.prompts.append(prompt)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def make_spec():
    return EngineeringSpecification(
        specification_id="espec-a4-test",
        task_id="task-a4-test",
        objective="Create a greeting module",
        workspace="D:/sandbox/project",
        requirements=(Requirement(
            requirement_id="REQ-1",
            description="Expose a greeting function",
            kind=RequirementKind.FUNCTIONAL,
            acceptance_criteria=("greet returns a greeting",),
        ),),
        implementation_units=(ImplementationUnit(
            unit_id="greeting",
            title="Greeting module",
            target_path="src/greeting.py",
            satisfies=("REQ-1",),
            acceptance_criteria=("Define greet()",),
        ),),
    )


def valid_response(content="def greet():\n    return 'hello'\n"):
    return {"artifacts": [{"path": "src/greeting.py", "content": content}]}


def test_generates_one_artifact_without_writing_files():
    client = FakeClient([valid_response()])
    generator = UnitCodeGenerator(client)
    result = generator.generate(make_spec(), "greeting")
    assert result.unit_id == "greeting"
    assert result.path == "src/greeting.py"
    assert "def greet" in result.content
    assert len(client.prompts) == 1
    assert "REQ-1" in client.prompts[0]
    assert generator.last_trace[0].succeeded is True


def test_includes_dependency_artifacts_and_project_context():
    spec = EngineeringSpecification(
        specification_id="espec-deps", task_id="task-deps", objective="Build app",
        workspace="D:/sandbox/project",
        implementation_units=(
            ImplementationUnit(unit_id="base", title="Base", target_path="src/base.py"),
            ImplementationUnit(unit_id="consumer", title="Consumer", target_path="src/consumer.py", depends_on=("base",)),
        ),
    )
    client = FakeClient([{"artifacts": [{"path": "src/consumer.py", "content": "from base import value\n"}]}])
    UnitCodeGenerator(client).generate(
        spec, "consumer", dependency_artifacts={"base": "value = 42\n"},
        project_files={"src/existing.py": "EXISTING_SENTINEL = True\n"},
    )
    assert "value = 42" in client.prompts[0]
    assert "EXISTING_SENTINEL" in client.prompts[0]


def test_rejects_unknown_unit_before_calling_model():
    client = FakeClient([])
    with pytest.raises(Exception, match="unknown implementation unit"):
        UnitCodeGenerator(client).generate(make_spec(), "missing")
    assert client.prompts == []


@pytest.mark.parametrize("response, message", [
    ([], "JSON object"),
    ({}, "only the 'artifacts' field"),
    ({"artifacts": []}, "exactly one artifact"),
    ({"artifacts": [{"path": "elsewhere.py", "content": "x"}]}, "exactly match"),
    ({"artifacts": [{"path": "src/greeting.py", "content": "  "}]}, "non-empty"),
    ({"artifacts": [{"path": "src/greeting.py", "content": "```python\\npass\\n```"}]}, "Markdown code fence"),
    ({"artifacts": [{"path": "src/greeting.py", "content": "pass", "extra": 1}]}, "exactly 'path' and 'content'"),
])
def test_rejects_malformed_or_unauthorized_output(response, message):
    client = FakeClient([response])
    with pytest.raises(CodeGenerationError, match=message):
        UnitCodeGenerator(client, max_attempts=1).generate(make_spec(), "greeting")


def test_retries_invalid_output_with_diagnostic_then_succeeds():
    client = FakeClient([
        {"artifacts": [{"path": "wrong.py", "content": "pass"}]},
        valid_response(),
    ])
    generator = UnitCodeGenerator(client, max_attempts=2)
    result = generator.generate(make_spec(), "greeting")
    assert result.path == "src/greeting.py"
    assert len(client.prompts) == 2
    assert "artifact path must exactly match" in client.prompts[1]
    assert [item.succeeded for item in generator.last_trace] == [False, True]


def test_stops_after_bounded_attempts():
    client = FakeClient([{}, {}, {}])
    generator = UnitCodeGenerator(client, max_attempts=2)
    with pytest.raises(CodeGenerationError, match="after 2 attempt"):
        generator.generate(make_spec(), "greeting")
    assert len(client.prompts) == 2
    assert len(generator.last_trace) == 2


def test_rejects_invalid_client_and_attempt_budget():
    with pytest.raises(TypeError, match="generate_json"):
        UnitCodeGenerator(object())
    with pytest.raises(ValueError, match="between 1 and 5"):
        UnitCodeGenerator(FakeClient([]), max_attempts=0)
