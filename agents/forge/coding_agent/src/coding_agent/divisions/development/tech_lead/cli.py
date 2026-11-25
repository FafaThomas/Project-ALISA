"""Run the Tech Lead (specialist) on one task from the command line.

    python cli.py D:/work/app "<task>" [--propose-only] [--json]

Same options as `coding-agent run`, already scoped to this unit.
"""

from coding_agent.unit_entry import run_unit_cli

if __name__ == "__main__":
    raise SystemExit(run_unit_cli(__file__))
