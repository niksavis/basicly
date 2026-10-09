from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from basicly import __version__

REPO_ROOT = Path(__file__).parent.parent
PROBE = (
    "import json, sys\n"
    "from basicly import entry\n"
    "code = entry.main(sys.argv[1:])\n"
    "loaded = [name for name in ('basicly.cli', 'basicly.board_cli', 'basicly.config')"
    " if name in sys.modules]\n"
    "sys.stderr.write(json.dumps({'code': code, 'loaded': loaded}))\n"
)


def _probe(*arguments: str) -> tuple[str, dict]:
    completed = subprocess.run(
        [sys.executable, "-c", PROBE, *arguments],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.stdout, json.loads(completed.stderr.strip().splitlines()[-1])


def test_a_tracker_read_loads_no_harness_module() -> None:
    out, seen = _probe("tracker", "list", "--status", "closed", "--limit", "1")
    assert seen["code"] == 0
    assert json.loads(out)["count"] == 1
    assert seen["loaded"] == ["basicly.config"]


def test_version_loads_no_harness_module() -> None:
    out, seen = _probe("--version")
    assert out == f"basicly {__version__}\n"
    assert seen == {"code": 0, "loaded": []}


def test_other_commands_run_through_the_full_cli() -> None:
    _, seen = _probe("check")
    assert "basicly.cli" in seen["loaded"]
