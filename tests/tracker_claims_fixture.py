from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from tests.tracker_process_fixture import INVEST


def _kit(repo: Path, *args: str) -> dict:
    done = subprocess.run(  # nosec B603
        [sys.executable, ".basicly/kit/tracker/cli.py", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    return json.loads(done.stdout)


def _review(repo: Path, record: str) -> None:
    noted = _kit(
        repo, "comment", ".basicly/ledger", record, "Agree the criterion: it knows the holder."
    )
    assert "refused" not in noted
    shown = _kit(repo, "show", ".basicly/ledger", record)
    evidence = {
        "invest": dict(INVEST),
        "conversation": [shown["comment_log"][-1]["seq"]],
        "checks": [
            {
                "criterion": "it knows",
                "command": [
                    Path(sys.executable).name,
                    ".basicly/kit/tracker/cli.py",
                    "show",
                    ".basicly/ledger",
                    record,
                ],
                "expected": "The shown holder names the claimant.",
            }
        ],
    }
    reviewed = _kit(repo, "review", ".basicly/ledger", record, "--evidence", json.dumps(evidence))
    assert "refused" not in reviewed
