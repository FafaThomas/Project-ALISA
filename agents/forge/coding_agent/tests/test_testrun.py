"""The Debugging Specialist's test run: hangs and crashes go back for repair; the workspace is never touched."""

import pytest
from conftest import ScriptedLLM

from coding_agent import CodingAgent, CodingRequest, InvocationContext, InvocationSource
from coding_agent.core import testrun
from coding_agent.core.models import FileChange
from coding_agent.core.workspace import ChangeSet, Workspace

UI = InvocationContext(source=InvocationSource.UI)

DEADLOCK = '''import threading, sys
lock = threading.Lock()

def save():
    with lock:
        pass

def add():
    with lock:
        save()          # acquires the same lock again: hangs

if __name__ == "__main__":
    add()
    print("added")
'''
FIXED = '''import threading, sys
lock = threading.Lock()

def save():
    pass

def add():
    with lock:
        save()

if __name__ == "__main__":
    add()
    print("added")
'''
CRASH = 'if __name__ == "__main__":\n    print("x".isoformat())\n'
CLEAN_ERROR = 'import sys\nif __name__ == "__main__":\n    print("Error: ID 99 not found", file=sys.stderr)\n    sys.exit(1)\n'


@pytest.fixture(autouse=True)
def test_run_on(monkeypatch):
    monkeypatch.setenv("CODING_AGENT_TEST_RUN", "repair")
    monkeypatch.setenv("CODING_AGENT_TEST_TIMEOUT", "3")


def debugger(commands, repaired=None):
    def handle(prompt, schema):
        if "commands" in (schema or {}).get("properties", {}):
            return {"summary": "plan", "commands": [{"command": c, "purpose": "try it"} for c in commands]}
        files = [{"path": "app.py", "action": "modify", "content": repaired}] if repaired else []
        return {"summary": "repair", "files": files}
    return handle


def run_app(tmp_path, first_version, commands, repaired=None, **request):
    handlers = {"python_developer": {"summary": "wrote", "files": [
        {"path": "app.py", "action": "create", "content": first_version}]},
        "debugging_specialist": debugger(commands, repaired)}
    llm = ScriptedLLM(handlers)
    report = CodingAgent.with_client(llm).unit("python_developer").run(
        CodingRequest(task="Write app.py", workspace=str(tmp_path / "ws"), **request), UI)
    return report, llm


def test_a_hang_is_caught_and_sent_for_repair(tmp_path):
    report, llm = run_app(tmp_path, DEADLOCK, ["python app.py"], repaired=FIXED)
    assert report.status == "completed", report.error
    repair = next(c["prompt"] for c in llm.calls if c["role"] == "debugging_specialist"
                  and "commands" not in (c["schema"] or {}).get("properties", {}))
    assert "[test-run]" in repair and "hung: no exit within 3s" in repair
    assert (tmp_path / "ws" / "app.py").read_text(encoding="utf-8") == FIXED
    assert report.test_run[0]["exit_code"] == 0 and "added" in report.test_run[0]["stdout"]
    assert not report.warnings


def test_a_crash_is_reported_with_its_traceback(tmp_path):
    report, llm = run_app(tmp_path, CRASH, ["python app.py"])
    repair = [c["prompt"] for c in llm.calls if "commands" not in (c["schema"] or {}).get("properties", {})
              and c["role"] == "debugging_specialist"][0]
    assert "crashed with a traceback" in repair and "AttributeError" in repair and "app.py" in repair


def test_leftover_failures_warn_by_default_and_block_on_request(tmp_path, monkeypatch):
    report, _ = run_app(tmp_path / "a", CRASH, ["python app.py"])
    assert report.status == "completed"
    assert any(w.startswith("test run: `python app.py` crashed") for w in report.warnings)
    assert (tmp_path / "a" / "ws" / "app.py").exists()

    monkeypatch.setenv("CODING_AGENT_TEST_RUN", "block")
    report, _ = run_app(tmp_path / "b", CRASH, ["python app.py"])
    assert report.status == "failed" and not (tmp_path / "b" / "ws" / "app.py").exists()


def test_a_clean_error_exit_is_not_a_failure(tmp_path):
    report, llm = run_app(tmp_path, CLEAN_ERROR, ["python app.py"])
    assert report.status == "completed" and not report.warnings
    assert report.test_run[0]["exit_code"] == 1
    assert sum(c["role"] == "debugging_specialist" for c in llm.calls) == 1  # planned, nothing to repair


def test_off_skips_the_test_run(tmp_path, monkeypatch):
    monkeypatch.setenv("CODING_AGENT_TEST_RUN", "off")
    report, llm = run_app(tmp_path, DEADLOCK, ["python app.py"])
    assert report.status == "completed" and report.test_run == []
    assert "debugging_specialist" not in llm.roles_called()


def test_only_project_python_commands_run(tmp_path):
    changes = ChangeSet(Workspace(tmp_path))
    changes.add(FileChange("app.py", "create", 'print("hi")\n'))
    results = testrun.run_commands(changes, [
        {"command": "pip install requests"}, {"command": "python ../outside.py"},
        {"command": "python -c \"print(1)\""}, {"command": "rm -rf ."}, {"command": "python missing.py"},
        {"command": "python app.py delete <ID_OF_FIRST>"}, {"command": "python app.py"}], 5)
    assert [bool(r.skipped) for r in results] == [True, True, True, True, True, True, False]
    assert "placeholder" in results[5].skipped
    assert results[-1].stdout.strip() == "hi"


def test_sandbox_has_no_secrets_and_leaves_the_workspace_alone(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "super-secret")
    monkeypatch.setenv("CODING_AGENT_API_TOKEN", "token")
    script = ('import os, pathlib\n'
              'print(os.environ.get("GEMINI_API_KEY"), os.environ.get("CODING_AGENT_API_TOKEN"))\n'
              'print(pathlib.Path.home().parent.name.startswith("coding-agent-testrun-"))\n'
              'pathlib.Path("data.json").write_text("{}")\n')
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / ".env").write_text("GEMINI_API_KEY=x\n", encoding="utf-8")
    changes = ChangeSet(Workspace(ws))
    changes.add(FileChange("app.py", "create", script))
    result = testrun.run_commands(changes, [{"command": "python app.py"}, {"command": "python app.py"}], 5)[0]
    assert result.stdout.split() == ["None", "None", "True"]
    assert not (ws / "data.json").exists() and not (ws / "app.py").exists()


# A game loop that waits for its player. The UI library is imported by a project module, not the script.
GAME_MAIN = 'from game import play\n\nif __name__ == "__main__":\n    play()\n'
GAME = ('import time\ntry:\n    import curses  # the UI; not available on every platform\nexcept ImportError:\n'
        '    curses = None\n\n\ndef play():\n    while True:  # until the player quits\n        time.sleep(0.05)\n')


def test_a_game_waiting_for_its_player_is_not_a_hang(tmp_path):
    changes = ChangeSet(Workspace(tmp_path / "ws"))
    changes.add(FileChange("main.py", "create", GAME_MAIN))
    changes.add(FileChange("game.py", "create", GAME))
    changes.add(FileChange("loop.py", "create", 'import time\nwhile True:\n    time.sleep(0.05)\n'))
    game, plain = testrun.run_commands(changes, [{"command": "python main.py"}, {"command": "python loop.py"}], 2)
    assert game.timed_out and not game.failed and "curses program that runs until its user quits" in game.unavailable
    assert plain.timed_out and plain.failed  # no UI: still a hang
    assert [p["path"] for p in testrun.problems_from([game, plain], changes, 2)] == ["loop.py"]


@pytest.mark.parametrize("source, library", [
    ("import os, pygame\n", "pygame"), ("import pygame as pg\n", "pygame"), ("from pygame.locals import *\n", "pygame"),
    ("import tkinter as tk\n", "tkinter"), ("from PySide6.QtWidgets import QApplication\n", "PySide6"),
    ("from .screens import menu\n", "arcade"), ("import pygamer\nimport json\n", ""), ("print('hi')\n", "")])
def test_interactive_libraries_are_found_through_project_imports(tmp_path, source, library):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "screens.py").write_text("import arcade\n", encoding="utf-8")
    (tmp_path / "app" / "main.py").write_text(source, encoding="utf-8")
    assert testrun.interactive_library(tmp_path / "app" / "main.py", tmp_path) == library


# A terminal game with an event loop (getch) that quits before playing when it "detects a test".
QUITTER = ('import os, sys\ntry:\n    import curses\nexcept ImportError:\n    curses = None\n\n\n'
           'def play(screen):\n    while screen.getch() != ord("q"):\n        pass\n\n\n'
           'if __name__ == "__main__":\n    if "SDL_VIDEODRIVER" in os.environ:\n        sys.exit(EXIT)  # "headless"\n'
           '    curses.wrapper(play)\n')
# The same game, fixed: it waits for its player (here: until the test run's time is up).
WAITER = ('import time\ntry:\n    import curses\nexcept ImportError:\n    curses = None\n\n\n'
          'def play(screen):\n    while screen.getch() != ord("q"):\n        pass\n\n\n'
          'if __name__ == "__main__":\n    while True:\n        time.sleep(0.05)\n')


def test_a_game_that_closes_by_itself_is_a_bug(tmp_path):
    changes = ChangeSet(Workspace(tmp_path / "ws"))
    changes.add(FileChange("game.py", "create", QUITTER.replace("EXIT", "0")))
    changes.add(FileChange("crash_game.py", "create", QUITTER.replace("EXIT", "3")))
    changes.add(FileChange("render.py", "create", "try:\n    import curses\nexcept ImportError:\n    pass\nprint('drawn')\n"))
    quits, errors, helped, render = testrun.run_commands(changes, [
        {"command": "python game.py"}, {"command": "python crash_game.py"},
        {"command": "python game.py --help"}, {"command": "python render.py"}], 5)
    assert quits.failed and "this curses program closed on its own after" in quits.reported
    assert "at game over, after a number of steps or frames, or when it detects a test" in quits.reported
    assert errors.failed and "stopped with exit code 3" in errors.reported
    assert not helped.failed  # started with arguments: ending is its answer
    assert not render.failed  # no event loop: drawing and exiting is all it does
    problems = testrun.problems_from([quits, errors, helped, render], changes, 5)
    assert [p["path"] for p in problems] == ["game.py", "crash_game.py"]
    assert problems[0]["detail"].startswith("`python game.py` failed: this curses program closed on its own")


# Two files that disagree (a flag called as a method), behind a catch-all that prints only the message.
INPUT = 'class Input:\n    def __init__(self):\n        self.quit_requested = False\n'
LOOP = ('try:\n    import curses\nexcept ImportError:\n    curses = None\nfrom keys import Input\n\n\n'
        'def run(screen=None):\n    keys = Input()\n    while not keys.quit_requested():\n        screen.getch()\n')
CATCH_ALL_MAIN = ('from loop import run\n\n\ndef main():\n    try:\n        run()\n    except Exception as e:\n'
                  '        print(f"An unexpected error occurred: {e}")\n    print("Closed gracefully.")\n\n\n'
                  'if __name__ == "__main__":\n    main()\n')


def test_an_error_hidden_by_a_catch_all_is_shown_with_its_traceback(tmp_path):
    changes = ChangeSet(Workspace(tmp_path / "ws"))
    for path, content in (("keys.py", INPUT), ("loop.py", LOOP), ("main.py", CATCH_ALL_MAIN)):
        changes.add(FileChange(path, "create", content))
    result = testrun.run_commands(changes, [{"command": "python main.py"}], 5)[0]
    assert "closed on its own" in result.reported and "An unexpected error occurred" in result.stdout
    assert testrun.CAUGHT_MARK in result.stderr
    assert 'File "loop.py", line 10, in run' in result.stderr and "TypeError: 'bool' object is not callable" in result.stderr
    assert str(tmp_path) not in result.stderr  # paths are the project's, not the throwaway copy's
    [problem] = testrun.problems_from([result], changes, 5)
    assert problem["path"] == "loop.py"  # where it failed, not the catch-all in main.py
    assert problem["detail"].startswith("`python main.py` failed: this curses program closed on its own")
    assert "TypeError: 'bool' object is not callable" in problem["detail"]
    assert "because of the exception below" in problem["detail"] and "at game over" not in problem["detail"]


def test_a_game_that_closed_by_itself_goes_to_repair_until_it_waits(tmp_path):
    report, llm = run_app(tmp_path, QUITTER.replace("EXIT", "0"), ["python app.py"], repaired=WAITER)
    repair = next(c["prompt"] for c in llm.calls if c["role"] == "debugging_specialist"
                  and "commands" not in (c["schema"] or {}).get("properties", {}))
    assert "[test-run]" in repair and "closed on its own" in repair
    assert report.status == "completed" and not report.warnings
    assert "runs until its user quits" in report.test_run[0]["unavailable"]


def test_sdl_programs_run_without_a_window_or_sound(tmp_path):
    env = testrun.sandbox_env(tmp_path / "project")
    assert env["SDL_VIDEODRIVER"] == "dummy" and env["SDL_AUDIODRIVER"] == "dummy"


def test_the_same_failure_after_a_repair_is_called_out(tmp_path):
    report, llm = run_app(tmp_path, CRASH, ["python app.py"])  # the repairs change nothing
    repairs = [c["prompt"] for c in llm.calls if c["role"] == "debugging_specialist"
               and "commands" not in (c["schema"] or {}).get("properties", {})]
    assert len(repairs) == 2
    assert "STILL FAILING" not in repairs[0]
    assert "STILL FAILING after repair round 1: the change made then did not fix it" in repairs[1]
    assert report.verification.problems == [] and report.warnings  # left as a warning, as before


def test_builders_and_repairers_may_not_game_the_checks():
    from coding_agent.core.prompts import _KIND_INSTRUCTIONS, NO_GAMING_THE_CHECKS, TEST_PLAN_INSTRUCTIONS
    assert NO_GAMING_THE_CHECKS in _KIND_INSTRUCTIONS["builder"] and NO_GAMING_THE_CHECKS in _KIND_INSTRUCTIONS["repairer"]
    assert "a game or a desktop or terminal UI" in TEST_PLAN_INSTRUCTIONS


def test_fallback_runs_help_on_entry_points(tmp_path):
    changes = ChangeSet(Workspace(tmp_path))
    changes.add(FileChange("main.py", "create", 'if __name__ == "__main__":\n    pass\n'))
    changes.add(FileChange("lib.py", "create", "x = 1\n"))
    assert testrun.fallback_commands(changes) == [{"command": "python main.py --help",
                                                   "purpose": "starts without crashing"}]
