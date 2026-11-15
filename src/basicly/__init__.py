import sys

MINIMUM_PYTHON = (3, 14)

if sys.version_info < MINIMUM_PYTHON:
    raise SystemExit(
        "basicly requires Python 3.14 or later. Run `uvx --python '>=3.14' basicly` "
        "to provision a suitable runtime."
    )

__version__ = "0.45.0"
