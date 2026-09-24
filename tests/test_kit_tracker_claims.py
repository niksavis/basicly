from __future__ import annotations

import json
import shutil
import subprocess  # nosec B404
import sys
from pathlib import Path

import pytest

KIT_DIR = Path(__file__).parent.parent / ".basicly" / "core" / "kit" / "tracker"
SHAPED = [
    "--description",
    "When I commit work, I want the tracker to know who holds it, so I can trust it.",
    "--acceptance",
    "- [ ] it knows",
    "--requirements",
    "- none",
]


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # nosec B603 B607
        ["git", *args], cwd=repo, capture_output=True, text=True, check=False
    )


def _kit(repo: Path, *args: str) -> dict:
    done = subprocess.run(  # nosec B603
        [sys.executable, ".basicly/kit/tracker/cli.py", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    return json.loads(done.stdout)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "consumer"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "Dev A")
    _git(root, "config", "user.email", "dev-a@example.invalid")
    ignored = shutil.ignore_patterns("__pycache__")
    shutil.copytree(KIT_DIR, root / ".basicly" / "kit" / "tracker", ignore=ignored)
    (root / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
    done = subprocess.run(  # nosec B603
        [sys.executable, ".basicly/kit/tracker/install_hook.py", "--root", "."],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    return root


def test_a_standalone_repository_refuses_code_on_a_record_nobody_holds(repo: Path) -> None:
    _git(repo, "add", "-A")
    installed = _git(repo, "commit", "-q", "-m", "chore: install the tracker kit")
    assert installed.returncode == 0, installed.stderr
    record = _kit(repo, "create", ".basicly/ledger", "--prefix", "acme", "--title", "x", *SHAPED)
    _git(repo, "add", "-A")
    filed = _git(repo, "commit", "-q", "-m", f"chore: file {record['record']}")
    assert filed.returncode == 0, filed.stderr

    (repo / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(repo, "add", "app.py")
    refused = _git(repo, "commit", "-q", "-m", f"feat: add value {record['record']}")
    assert refused.returncode != 0
    assert "Claim the record first" in refused.stderr

    _kit(repo, "claim", ".basicly/ledger", record["record"])
    _git(repo, "add", "-A")
    landed = _git(repo, "commit", "-q", "-m", f"feat: add value {record['record']}")
    assert landed.returncode == 0, landed.stderr


def test_the_installer_removes_only_its_claim_block(repo: Path) -> None:
    hook = repo / ".git" / "hooks" / "commit-msg"
    assert "basicly-tracker claim" in hook.read_text(encoding="utf-8")

    subprocess.run(  # nosec B603
        [sys.executable, ".basicly/kit/tracker/install_hook.py", "--root", ".", "--uninstall"],
        cwd=repo,
        capture_output=True,
        check=False,
    )

    assert not hook.exists()
