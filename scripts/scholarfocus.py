#!/usr/bin/env python3
"""DEPRECATED entry point. Use `python -m scholarlib.cli.scholarfocus` instead."""

import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

warnings.warn(
    "scripts/scholarfocus.py is deprecated; use "
    "`python -m scholarlib.cli.scholarfocus`",
    DeprecationWarning,
    stacklevel=2,
)

from scholarlib.cli.scholarfocus import main  # noqa: E402

if __name__ == "__main__":
    main()
