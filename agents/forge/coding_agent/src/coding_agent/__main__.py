"""``python -m coding_agent``: the agent-level command line (same as ``coding-agent``)."""

import sys

from .cli import main

sys.exit(main())
