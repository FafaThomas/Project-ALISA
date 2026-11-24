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
