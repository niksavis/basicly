from __future__ import annotations

import json
import subprocess  # nosec B404
import sys
from pathlib import Path

import pytest

SCRIPT_PATH = (
    Path(__file__).resolve().parents[2] / ".basicly" / "core" / "hooks" / "lefthook-guard.py"
)


def _checkout(root: Path) -> Path:
    (root / ".basicly").mkdir(parents=True)
    (root / ".basicly" / "lefthook.yml").write_text("pre-commit: {}\n", encoding="utf-8")
    return root


def _run(command: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    payload = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd)}
    return subprocess.run(  # nosec B603
        [sys.executable, str(SCRIPT_PATH)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize(
    "command",
    [
        ".basicly/bin/lefthook run commit-msg /home/user/.cache/msg.txt",
        "lefthook install",
        ".basicly/bin/lefthook install --force",
        "npx lefthook run pre-commit",
        "cd lane && LEFTHOOK_VERBOSE=1 lefthook run pre-push",
    ],
)
def test_a_lefthook_run_that_rewrites_the_shared_hooks_is_refused(
    tmp_path: Path, command: str
) -> None:
    done = _run(command, _checkout(tmp_path / "repo") / "sub")

    assert done.returncode == 2
    assert "python3 .basicly/core/hooks/HOOK.py FILE" in done.stderr


@pytest.mark.parametrize(
    "command",
    [
        ".basicly/bin/lefthook run pre-commit --no-auto-install --no-tty",
        "lefthook version",
        "lefthook --help",
        "echo lefthook run commit-msg",
        "rg 'lefthook install' docs",
    ],
)
def test_a_lefthook_call_that_leaves_the_hooks_alone_passes(tmp_path: Path, command: str) -> None:
    assert _run(command, _checkout(tmp_path / "repo")).returncode == 0


def test_outside_a_basicly_checkout_the_guard_stays_out_of_the_way(tmp_path: Path) -> None:
    assert _run("lefthook install", tmp_path).returncode == 0


def test_a_payload_that_is_not_a_bash_call_passes(tmp_path: Path) -> None:
    done = subprocess.run(  # nosec B603
        [sys.executable, str(SCRIPT_PATH)],
        input=json.dumps({"tool_name": "Read", "cwd": str(_checkout(tmp_path / "repo"))}),
        capture_output=True,
        text=True,
        check=False,
    )

    assert done.returncode == 0
