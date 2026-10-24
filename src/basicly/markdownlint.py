from __future__ import annotations

import sys
from pathlib import Path

from . import checkout
from .catalog import bundled_catalog_root
from .hooks import load_hook_specs, selected_hook_specs


def require_prerequisites(repo_root: Path, selection: frozenset[str] | None) -> None:
    selected = selected_hook_specs(load_hook_specs(), selection)
    if not any(spec.id == "markdownlint" for spec in selected):
        return
    result = checkout.run(
        [
            sys.executable,
            str(bundled_catalog_root() / "hooks" / "markdownlint.py"),
            "--check-prerequisites",
        ],
        cwd=repo_root,
        check=False,
    )
    if result.stdout:
        print(result.stdout, end="")
    if result.returncode != 0:
        raise ValueError(result.stderr.strip() or "markdownlint prerequisite check failed")
