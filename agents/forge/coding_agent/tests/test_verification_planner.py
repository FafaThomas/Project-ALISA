import subprocess
import sys

from coding_agent.models import ChangeType, FileChange
from coding_agent.verification_planner import VerificationPlanner


def test_empty_python_workspace_uses_in_memory_syntax_check(tmp_path):
    change = FileChange(
        path="hello.py",
        change_type=ChangeType.CREATE,
        reason="Create greeting module",
        content="def greet():\n    return 'Hello, ALISA!'\n",
    )
    planner = VerificationPlanner(tmp_path)

    commands = planner.build((change,), (("python", "-m", "pytest", "-q"),))

    assert len(commands) == 1
    assert commands[0][0:2] == ("python", "-c")
    assert "pytest" not in commands[0]

    (tmp_path / "hello.py").write_text(change.content, encoding="utf-8")
    result = subprocess.run(commands[0], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "__pycache__").exists()


def test_invalid_python_source_fails_without_creating_bytecode(tmp_path):
    change = FileChange(
        path="broken.py",
        change_type=ChangeType.CREATE,
        reason="Create broken module",
        content="def greet(:\n    pass\n",
    )
    (tmp_path / "broken.py").write_text(change.content, encoding="utf-8")
    command = VerificationPlanner(tmp_path).build((change,))[0]

    result = subprocess.run(command, capture_output=True, text=True)

    assert result.returncode == 1
    assert "Python syntax check failed" in result.stderr
    assert not (tmp_path / "__pycache__").exists()


def test_existing_pytest_suite_is_run_without_cache_provider(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_sample.py").write_text("def test_ok(): pass\n")
    change = FileChange(
        path="module.py",
        change_type=ChangeType.CREATE,
        reason="Create module",
        content="VALUE = 1\n",
    )

    commands = VerificationPlanner(tmp_path).build((change,))

    assert commands[-1] == (
        "python", "-m", "pytest", "-q", "-p", "no:cacheprovider"
    )


def test_proposed_pytest_file_enables_pytest_verification(tmp_path):
    from coding_agent.models import ChangeType, FileChange
    from coding_agent.verification_planner import VerificationPlanner

    proposed_test = FileChange(
        path="tests/test_behavior.py",
        change_type=ChangeType.CREATE,
        reason="Add behavioral acceptance test",
        content="def test_behavior(): assert True\n",
    )

    commands = VerificationPlanner(tmp_path).build([proposed_test])

    assert any(command[:4] == ("python", "-m", "pytest", "-q") for command in commands)


def test_react_workspace_adds_structural_verification(tmp_path):
    (tmp_path / "package.json").write_text('{"scripts":{"build":"vite build"}}', encoding="utf-8")
    (tmp_path / "App.jsx").write_text("export default function App(){ return <div/> }", encoding="utf-8")
    change = FileChange(
        path="App.jsx",
        change_type=ChangeType.MODIFY,
        reason="Update app",
        content="export default function App(){ return <div/> }",
    )

    commands = VerificationPlanner(tmp_path).build((change,))

    assert ("python", "-m", "coding_agent.structural_verifier", str(tmp_path.resolve())) in commands


def test_malformed_model_suggestions_do_not_poison_deterministic_check(tmp_path):
    change = FileChange(
        path="hello.py",
        change_type=ChangeType.CREATE,
        reason="Create greeting module",
        content="VALUE = 1\n",
    )
    commands = VerificationPlanner(tmp_path).build(
        (change,),
        suggested_commands=(
            ("python", " ", "backend/manage.py", "check"),
            "python backend/manage.py check",
            ("python", "-m", "pytest", "-q"),
        ),
    )

    assert commands[0][0:2] == ("python", "-c")
    assert ("python", "-m", "pytest", "-q", "-p", "no:cacheprovider") not in commands
    assert all(all(part.strip() for part in command) for command in commands)
