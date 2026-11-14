from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_kit_ledger_freeze import KIT, _migration
from tests.test_kit_vault_share import bare

__all__ = ["bare"]
PLACED = Path(".basicly/kit/tracker")


def _run(project: Path, *argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv), cwd=project, env=dict(os.environ), capture_output=True, text=True, check=False
    )


def _kit(project: Path, *argv: str) -> dict:
    done = _run(project, sys.executable, str(project / PLACED / "cli.py"), *argv)
    assert done.returncode == 0, done.stdout + done.stderr
    return json.loads(done.stdout)


def _frozen_consumer(
    tmp_path: Path, bare: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path]:
    ledger, state = _migration(tmp_path, bare, monkeypatch)
    project = ledger.parent.parent
    for argv in (
        ("init", "-q"),
        ("config", "user.name", "hook-owner"),
        ("config", "user.email", "hook-owner@example.invalid"),
        ("config", "basicly.holder", "hook-owner"),
    ):
        done = _run(project, "git", *argv)
        assert done.returncode == 0, done.stderr
    monkeypatch.setenv("BASICLY_HOLDER", "hook-owner")
    shutil.copytree(KIT, project / PLACED, ignore=shutil.ignore_patterns("__pycache__"))
    installed = _run(
        project, sys.executable, str(project / PLACED / "install_hook.py"), "--root", "."
    )
    assert installed.returncode == 0, installed.stdout + installed.stderr
    _kit(
        project,
        "migrate",
        str(ledger),
        "--to-vault",
        "--freeze",
        "--prefix",
        "demo",
        "--vault",
        str(bare),
        "--state",
        str(state),
    )
    return project, state


@pytest.mark.parametrize("claimed", [False, True])
@pytest.mark.parametrize("broken_first", [False, True])
def test_the_real_claim_hook_checks_canonical_ownership_with_a_file_guard(
    tmp_path: Path, bare: Path, monkeypatch: pytest.MonkeyPatch, claimed: bool, broken_first: bool
) -> None:
    project, _state = _frozen_consumer(tmp_path, bare, monkeypatch)
    if broken_first:
        binaries = tmp_path / "binaries"
        binaries.mkdir()
        for name, body in (
            ("python3", "#!/bin/sh\nexit 31\n"),
            (
                "python",
                "#!/bin/sh\nexec " + shlex.quote(Path(sys.executable).as_posix()) + ' "$@"\n',
            ),
        ):
            executable = binaries / name
            executable.write_text(body, encoding="utf-8")
            executable.chmod(0o755)
        monkeypatch.setenv("PATH", str(binaries) + os.pathsep + os.environ.get("PATH", ""))
    if claimed:
        _kit(project, "claim", "demo-a1b2")
        _kit(project, "update", ".basicly/ledger", "demo-a1b2", "--status", "in_progress")
        _kit(project, "sync", "--urgent")
    (project / "change.txt").write_text("A real code change", encoding="utf-8")
    staged = _run(project, "git", "add", "change.txt")
    assert staged.returncode == 0, staged.stderr

    committed = _run(project, "git", "commit", "-q", "-m", "fix: use the vault record (demo-a1b2)")

    if claimed:
        assert committed.returncode == 0, committed.stdout + committed.stderr
    else:
        assert committed.returncode == 1
        assert "Claim" in committed.stderr or "claim" in committed.stderr


def test_reinstalling_hooks_preserves_the_frozen_ledger_and_archive(
    tmp_path: Path, bare: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, _state = _frozen_consumer(tmp_path, bare, monkeypatch)
    before = {
        path.relative_to(project).as_posix(): path.read_bytes()
        for path in (project / ".basicly").rglob("*")
        if path.is_file() and not path.is_relative_to(project / PLACED)
    }

    installed = _run(
        project,
        sys.executable,
        str(project / PLACED / "install_hook.py"),
        "--root",
        ".",
        "--ledger",
        ".basicly/ledger",
        "--pin",
    )

    assert installed.returncode == 0, installed.stdout + installed.stderr
    assert {name: (project / name).read_bytes() for name in before} == before
    assert "basicly-tracker claim" in (project / ".git/hooks/commit-msg").read_text()


def test_a_fresh_worktree_uses_the_frozen_vault_without_a_directory_redirect(
    tmp_path: Path, bare: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, _state = _frozen_consumer(tmp_path, bare, monkeypatch)
    _kit(project, "claim", "demo-a1b2")
    _kit(project, "update", ".basicly/ledger", "demo-a1b2", "--status", "in_progress")
    for argv in (("add", "."), ("commit", "-q", "-m", "fix: preserve migration (demo-a1b2)")):
        done = _run(project, "git", *argv)
        assert done.returncode == 0, done.stdout + done.stderr
    lane = tmp_path / "fresh-lane"
    added = _run(project, "git", "worktree", "add", "-q", "-b", "fresh-lane", str(lane))
    assert added.returncode == 0, added.stderr
    archive = lane / ".basicly/ledger-archive"
    original = {path.name: path.read_bytes() for path in archive.iterdir() if path.is_file()}

    _kit(lane, "comment", ".basicly/ledger", "demo-a1b2", "From a fresh worktree")
    shown = _kit(project, "show", ".basicly/ledger", "demo-a1b2")

    assert "From a fresh worktree" in json.dumps(shown)
    assert (lane / ".basicly/ledger").is_file()
    assert {
        path.name: path.read_bytes() for path in archive.iterdir() if path.is_file()
    } == original


def test_an_existing_lane_redirect_can_reach_the_frozen_base(
    tmp_path: Path, bare: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, _state = _frozen_consumer(tmp_path, bare, monkeypatch)
    lane = tmp_path / "existing-lane"
    ledger = lane / ".basicly/ledger"
    ledger.mkdir(parents=True)
    (lane / ".git").write_text("gitdir: ../project/.git\n", encoding="utf-8")
    (ledger / "redirect").write_text(str(project), encoding="utf-8")
    shutil.copytree(KIT, lane / PLACED, ignore=shutil.ignore_patterns("__pycache__"))

    shown = _kit(lane, "show", ".basicly/ledger", "demo-a1b2")

    assert shown["record"] == "demo-a1b2"
