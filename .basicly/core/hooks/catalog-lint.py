"""Pre-commit hook: validate catalog YAML sources via ``basicly catalog-lint``.

Runs the CLI so the hook and the command share one implementation. Blocks a
commit that introduces a schema-invalid source, a discoverable-name source
(SKILL.md / *.fragment.md), or a stray .yml under the catalog.
"""

from __future__ import annotations

import subprocess  # nosec B404
import sys
from pathlib import Path


def main() -> int:
    """Run ``basicly catalog-lint`` from the repository root."""
    proc = subprocess.run(  # nosec B603
        [sys.executable, "-m", "basicly.cli", "catalog-lint"], cwd=Path.cwd(), check=False
    )
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
