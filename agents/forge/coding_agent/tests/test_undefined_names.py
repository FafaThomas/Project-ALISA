import pytest

from coding_agent.planner import _normalize_model_file_content, undefined_python_names


def test_finds_a_missing_import():
    # Regression: the calculator's CLI tests used subprocess without importing it.
    source = "import pytest\n\ndef test_cli():\n    result = subprocess.run(['python'])\n    assert result\n"

    assert undefined_python_names(source) == [("subprocess", 4)]


def test_accepts_valid_code():
    source = (
        "import os.path\n"
        "from math import sqrt as root\n"
        "import pytest\n\n"
        "LIMIT = 3\n\n"
        "class Calc:\n"
        "    def go(self, value, *args, **kwargs):\n"
        "        total = [n * 2 for n in range(LIMIT)]\n"
        "        try:\n"
        "            return root(value) + len(total) + len(os.path.sep)\n"
        "        except ValueError as error:\n"
        "            raise RuntimeError(str(error)) from error\n\n"
        "@pytest.fixture\n"
        "def calc():\n"
        "    return Calc()\n\n"
        "def test_go(calc):\n"
        "    assert calc.go(4) > 0 and __name__\n"
    )

    assert undefined_python_names(source) == []


def test_skips_files_with_star_imports():
    assert undefined_python_names("from calculator import *\n\nprint(add(1, 2))\n") == []


def test_generated_python_with_an_undefined_name_is_rejected():
    # The earlier calculator run's test called a function nothing defined.
    with pytest.raises(ValueError, match="uses names it never defines or imports: execute_operation"):
        _normalize_model_file_content(
            "tests/test_calc.py", "def test_op():\n    assert execute_operation('+', 1, 2) == 3\n"
        )


def test_non_python_files_are_not_checked():
    assert _normalize_model_file_content("README.md", "Run execute_operation()\n") == "Run execute_operation()\n"


BURGER_APP = (
    "from flask import Flask\n"
    "app = Flask(__name__)\n"
    "\n"
    "@app.before_request\n"
    "def handle_first_request():\n"
    "    create_tables()\n"
    "\n"
    "if __name__ == '__main__':\n"
    "    app.run(port=5000)\n"
    "\n"
    "\n"
    "def create_tables():\n"
    "    pass"
)


def test_main_guard_is_moved_below_later_definitions():
    # Regression: the burger repair defined create_tables after a blocking app.run(),
    # so running the script raised NameError on the first request.
    from coding_agent.planner import move_main_guard_to_end

    fixed = move_main_guard_to_end(BURGER_APP)

    assert fixed.index("def create_tables") < fixed.index("if __name__")
    assert fixed.rstrip().endswith("app.run(port=5000)")
    compile(fixed, "app.py", "exec")


def test_generated_content_gets_the_main_guard_repair():
    fixed = _normalize_model_file_content("backend/app.py", BURGER_APP)

    assert fixed.index("def create_tables") < fixed.index("if __name__")


def test_main_guard_already_last_is_left_alone():
    from coding_agent.planner import move_main_guard_to_end

    source = "def main():\n    pass\n\nif __name__ == '__main__':\n    main()\n"

    assert move_main_guard_to_end(source) == source
    assert move_main_guard_to_end("x = 1\n") == "x = 1\n"


def test_dropped_main_guard_is_restored():
    # Regression: the burger repair fixed the NameError by deleting app.run().
    from coding_agent.planner import restore_main_guard

    original = "app = 1\n\nif __name__ == '__main__':\n    app.run(port=5000)\n"
    updated = "app = 1\n\ndef create_tables():\n    pass\n"

    restored = restore_main_guard(original, updated)

    assert restored.startswith(updated.rstrip())
    assert restored.rstrip().endswith("if __name__ == '__main__':\n    app.run(port=5000)")
    compile(restored, "app.py", "exec")


def test_main_guard_is_not_restored_when_kept_or_never_present():
    from coding_agent.planner import restore_main_guard

    kept = "x = 1\nif __name__ == '__main__':\n    print(x)\n"
    assert restore_main_guard("if __name__ == '__main__':\n    pass\n", kept) == kept
    assert restore_main_guard("x = 1\n", "x = 2\n") == "x = 2\n"
