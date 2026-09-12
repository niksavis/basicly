from __future__ import annotations

import os
import subprocess  # nosec B404
import sys
from pathlib import Path


def _build(repo: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # nosec B603
        [sys.executable, "-m", "basicly.cli", "build"],
        cwd=repo,
        env={**os.environ, "PYTHONPATH": str(repo / "src")},
        capture_output=True,
        text=True,
        check=False,
    )


ALWAYS_ON_OUTPUTS = ("AGENTS.md", ".claude/CLAUDE.md", ".github/copilot-instructions.md")


def test_cli_build_keeps_a_hand_authored_file_it_has_never_seen(work_repo: Path) -> None:

    (work_repo / ".basicly/generated-manifest.json").unlink()
    for rel in ALWAYS_ON_OUTPUTS:
        (work_repo / rel).write_text(f"hand-written {rel}\n", encoding="utf-8")

    result = _build(work_repo)

    assert result.returncode == 0, result.stderr
    for rel in ALWAYS_ON_OUTPUTS:
        backup = work_repo / (rel + ".basicly-bak")
        assert backup.read_text(encoding="utf-8") == f"hand-written {rel}\n"
        assert rel in result.stderr
        assert (work_repo / rel).read_text(encoding="utf-8") != f"hand-written {rel}\n"


def test_cli_build_writes_no_second_backup_on_a_repeat_run(work_repo: Path) -> None:
    (work_repo / ".basicly/generated-manifest.json").unlink()
    for rel in ALWAYS_ON_OUTPUTS:
        (work_repo / rel).write_text(f"hand-written {rel}\n", encoding="utf-8")
    _build(work_repo)

    result = _build(work_repo)

    assert result.returncode == 0, result.stderr
    for rel in ALWAYS_ON_OUTPUTS:
        backup = work_repo / (rel + ".basicly-bak")
        assert backup.read_text(encoding="utf-8") == f"hand-written {rel}\n", "overwrote the copy"
        assert not (work_repo / (rel + ".basicly-bak.basicly-bak")).exists()
