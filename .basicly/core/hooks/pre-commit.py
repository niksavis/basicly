"""Run the configured fast checks before a commit.

Invoked by the pre-commit hook (via pre-commit or lefthook). Runs the
``[[verify.checks]]`` declared for mode ``fast`` in the repo's basicly.toml —
config-driven, so every consumer gates its own stack and a repo with no checks
configured passes with a note (it never fails on tooling it doesn't have).

First applies the ``fix_command`` a check may declare (a formatter's write mode)
to the staged files and re-stages them, so the commit already carries the fixed
bytes: nobody spends a review cycle re-running a repair a script can make. The
checks then run unchanged, so a non-mechanical failure (a lint or type error)
still blocks the commit and is reported.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_runner import apply_fixes, project_root, run_checks


def main() -> int:
    """Entry point for the pre-commit hook."""
    repo_root = project_root()
    apply_fixes(repo_root, "fast")
    return run_checks(repo_root, "fast")


if __name__ == "__main__":
    sys.exit(main())
