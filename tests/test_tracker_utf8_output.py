from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import TYPE_CHECKING

from tests.flipped_tracker import flipped_repo, seed_records
from tests.kit_deployment_helpers import KIT_RELATIVE, REPO_ROOT

if TYPE_CHECKING:
    from pathlib import Path

TITLE = "naïve → ✓ check"
CP1252 = {**os.environ, "PYTHONIOENCODING": "cp1252"}


def _piped(argv: list[str], cwd: Path) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(argv, cwd=cwd, env=CP1252, capture_output=True, check=False)


def test_the_kit_writes_utf8_on_a_cp1252_pipe(tmp_path: Path) -> None:
    cli = str(REPO_ROOT / KIT_RELATIVE / "cli.py")
    ledger = str(tmp_path / "ledger")
    created = _piped(
        [sys.executable, cli, "create", ledger, "--prefix", "demo", "--title", TITLE], tmp_path
    )
    assert created.returncode == 0, created.stderr
    listed = _piped([sys.executable, cli, "list", ledger], tmp_path)
    assert listed.returncode == 0, listed.stderr
    assert json.loads(listed.stdout.decode("utf-8"))["records"][0]["fields"]["title"] == TITLE


def test_the_engine_writes_utf8_on_a_cp1252_pipe(tmp_path: Path) -> None:
    repo = flipped_repo(tmp_path)
    seed_records(repo, [{"id": "basicly-aaa", "status": "open", "title": TITLE}])
    listed = _piped([sys.executable, "-m", "basicly.entry", "tracker", "list"], repo)
    assert listed.returncode == 0, listed.stderr
    assert json.loads(listed.stdout.decode("utf-8"))["records"][0]["fields"]["title"] == TITLE
