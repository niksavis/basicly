from __future__ import annotations

import json
import os
import subprocess  # nosec B404
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HOOK = ROOT / ".basicly" / "core" / "hooks" / "tracker-commit-msg.py"
KIT_CLI = ROOT / ".basicly" / "core" / "kit" / "tracker" / "cli.py"


def _run(cwd: Path, env: dict[str, str], *argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # nosec B603
        list(argv),
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )


def _kit(project: Path, env: dict[str, str], *argv: str) -> dict:
    done = _run(project, env, sys.executable, str(KIT_CLI), *argv)
    assert done.returncode == 0, done.stdout + done.stderr
    return json.loads(done.stdout)


def _project(tmp_path: Path) -> tuple[Path, dict[str, str], str]:
    project = tmp_path / "project"
    project.mkdir()
    env = {
        **os.environ,
        "BASICLY_TRACKER_STATE": str(tmp_path / "state"),
        "BASICLY_TRACKER_SESSION": "commit-msg-control",
    }
    env.pop("CI", None)
    env.pop("BASICLY_TRACKER_EPHEMERAL", None)
    done = _run(project, env, "git", "init", "-q")
    assert done.returncode == 0, done.stderr
    created = _kit(
        project, env, "create", ".basicly/ledger", "--prefix", "demo", "--title", "Known record"
    )
    return project, env, str(created["record"])


def _freeze(project: Path, env: dict[str, str], tmp_path: Path) -> None:
    vault = tmp_path / "vault.git"
    done = _run(tmp_path, env, "git", "init", "-q", "--bare", str(vault))
    assert done.returncode == 0, done.stderr
    state = env["BASICLY_TRACKER_STATE"]
    _kit(project, env, "vault-init", "--vault", str(vault), "--yes")
    _kit(
        project,
        env,
        "migrate",
        ".basicly/ledger",
        "--to-vault",
        "--vault",
        str(vault),
        "--state",
        state,
        "--no-map-kept",
    )
    _kit(
        project,
        env,
        "migrate",
        ".basicly/ledger",
        "--to-vault",
        "--freeze",
        "--prefix",
        "demo",
        "--vault",
        str(vault),
        "--state",
        state,
    )


def _commit_msg(
    project: Path, env: dict[str, str], message: str
) -> subprocess.CompletedProcess[str]:
    message_file = project.parent / "COMMIT_MSG"
    message_file.write_text(message + "\n", encoding="utf-8")
    return _run(project, env, sys.executable, str(HOOK), str(message_file))


@pytest.mark.parametrize("frozen", [False, True], ids=["local-ledger", "vault-frozen"])
def test_an_unknown_record_id_is_refused_before_and_after_a_vault_freeze(
    tmp_path: Path, frozen: bool
) -> None:
    project, env, known = _project(tmp_path)
    if frozen:
        _freeze(project, env, tmp_path)
        assert (project / ".basicly-tracker.toml").is_file()

    unknown = _commit_msg(project, env, "fix: use a missing record (demo-zzzzzz)")
    accepted = _commit_msg(project, env, f"fix: use the record ({known})")

    assert unknown.returncode == 1, unknown.stdout + unknown.stderr
    assert "unknown issue id: demo-zzzzzz" in unknown.stderr
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    assert "Issue id reference is valid." in accepted.stdout
