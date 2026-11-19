from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from basicly import ci_landing


def main() -> int:
    head = os.environ.get("GITHUB_SHA", "")
    proof = ci_landing.reusable(Path.cwd(), head, os.environ.get("GITHUB_RUN_ID", ""))
    reason = (
        f"reuse: {proof} proved this tree" if proof else "run the checks: no first-attempt pass"
    )
    print(f"ci-reuse: {reason}", file=sys.stderr)
    print(f"reuse={'true' if proof else 'false'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
