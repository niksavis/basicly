from __future__ import annotations

import json
import os
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


def test_the_first_commit_of_an_install_passes_with_a_record_already_filed(repo: Path) -> None:
    (repo / ".claude" / "skills" / "tracker").mkdir(parents=True)
    (repo / ".claude" / "skills" / "tracker" / "SKILL.md").write_text("x\n", encoding="utf-8")
    (repo / ".gitattributes").write_text("*.jsonl merge=union\n", encoding="utf-8")
    _kit(repo, "create", ".basicly/ledger", "--prefix", "acme", "--title", "first", *SHAPED)
    _git(repo, "add", "-A")

    installed = _git(repo, "commit", "-q", "-m", "chore: install the tracker kit")

    assert installed.returncode == 0, installed.stderr
    (repo / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(repo, "add", "app.py")
    refused = _git(repo, "commit", "-q", "-m", "feat: add value")
    assert refused.returncode != 0 and "Claim the record first" in refused.stderr


def _br_backlog(repo: Path, statuses: dict[str, str]) -> None:
    beads = repo / ".beads"
    beads.mkdir(exist_ok=True)
    (beads / "config.yaml").write_text("issue_prefix: acme\n", encoding="utf-8")
    lines = [
        json.dumps({"id": record, "title": f"br {record}", "status": status})
        for record, status in statuses.items()
    ]
    (beads / "issues.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _hook(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # nosec B603
        [sys.executable, ".basicly/kit/tracker/install_hook.py", "--root", ".", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )


def test_a_mirror_of_br_imports_it_and_installs_no_claim_gate(repo: Path) -> None:
    _br_backlog(repo, {"acme-aa11": "open", "acme-bb22": "in_progress"})

    mirrored = _hook(repo, "--mirror", "beads")

    assert mirrored.returncode == 0, mirrored.stderr
    assert (repo / ".basicly" / "ledger" / "mirror.json").is_file()
    assert "no claim gate is installed" in mirrored.stdout
    assert not (repo / ".git" / "hooks" / "commit-msg").exists()
    (repo / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(repo, "add", "-A")
    landed = _git(repo, "commit", "-q", "-m", "feat: work claimed in br only acme-aa11")
    assert landed.returncode == 0, landed.stderr


def test_sync_follows_br_for_status_and_new_records(repo: Path) -> None:
    _br_backlog(repo, {"acme-aa11": "open"})
    assert _hook(repo, "--mirror", "beads").returncode == 0
    _br_backlog(repo, {"acme-aa11": "closed", "acme-cc33": "open"})

    report = _kit(repo, "sync", ".basicly/ledger")

    assert report["imported"] == ["acme-cc33"]
    assert report["status_changed"] == [{"record": "acme-aa11", "was": "open", "now": "closed"}]
    assert _kit(repo, "show", ".basicly/ledger", "acme-aa11")["status"] == "closed"


def test_sync_warns_when_the_br_database_is_newer_than_its_export(repo: Path) -> None:
    _br_backlog(repo, {"acme-aa11": "open"})
    assert _hook(repo, "--mirror", "beads").returncode == 0
    database = repo / ".beads" / "beads.db"
    database.write_text("", encoding="utf-8")
    later = (repo / ".beads" / "issues.jsonl").stat().st_mtime + 60
    os.utime(database, (later, later))

    report = _kit(repo, "sync", ".basicly/ledger", "--dry-run")

    assert "br sync --flush-only" in report["stale"]


def test_ending_the_mirror_installs_the_claim_gate(repo: Path) -> None:
    _br_backlog(repo, {"acme-aa11": "open"})
    assert _hook(repo, "--mirror", "beads").returncode == 0
    kept = _hook(repo)
    assert "this ledger mirrors beads" in kept.stdout

    ended = _hook(repo, "--end-mirror")

    assert "the mirror ended" in ended.stdout
    assert not (repo / ".basicly" / "ledger" / "mirror.json").exists()
    assert "basicly-tracker claim" in (repo / ".git" / "hooks" / "commit-msg").read_text("utf-8")


def test_a_mirrored_record_keeps_the_br_update_time_not_the_import_time(repo: Path) -> None:
    beads = repo / ".beads"
    beads.mkdir()
    (beads / "config.yaml").write_text("issue_prefix: acme\n", encoding="utf-8")
    line = {
        "id": "acme-aa11",
        "title": "br record",
        "status": "open",
        "created_at": "2026-07-14T23:13:00Z",
        "updated_at": "2026-07-15T08:00:00Z",
    }
    (beads / "issues.jsonl").write_text(json.dumps(line) + "\n", encoding="utf-8")

    assert _hook(repo, "--mirror", "beads").returncode == 0

    dates = _kit(repo, "show", ".basicly/ledger", "acme-aa11")["dates"]
    assert dates["created"] == "2026-07-14T23:13:00Z"
    assert dates["updated"] == "2026-07-15T08:00:00Z"
