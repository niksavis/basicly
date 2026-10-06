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


def test_an_id_the_staged_ledger_does_not_hold_is_named_with_the_command_that_stages_it(
    repo: Path,
) -> None:
    _git(repo, "add", "-A")
    assert _git(repo, "commit", "-q", "-m", "chore: install the tracker kit").returncode == 0
    _kit(repo, "create", ".basicly/ledger", "--prefix", "acme", "--title", "x", *SHAPED)
    _git(repo, "add", "-A")
    assert _git(repo, "commit", "-q", "-m", "chore: file a record").returncode == 0
    (repo / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(repo, "add", "app.py")

    unknown = _git(repo, "commit", "-q", "-m", "feat: add value acme-zz99")
    unnamed = _git(repo, "commit", "-q", "-m", "feat: add-value with no id")

    assert (
        "names acme-zz99, which the staged ledger .basicly/ledger does not hold" in unknown.stderr
    )
    assert "git add .basicly/ledger" in unknown.stderr
    assert "it names no record id" in unnamed.stderr


def _use_gate(repo: Path, gate: str) -> None:
    if gate == "standalone":
        return
    source = KIT_DIR.parents[1] / "hooks" / "tracker-claim.py"
    text = source.read_text("utf-8")
    text = text.replace(
        'PLACES = ((Path(".basicly") / "core" / "kit" / "tracker" / "cli.py").as_posix(),)',
        'PLACES = ((Path(".basicly") / "kit" / "tracker" / "cli.py").as_posix(),)',
    )
    text = text.replace(
        'LOCATE = Path(__file__).resolve().parent.parent / "kit" / "tracker" / "locate.py"',
        'LOCATE = Path(__file__).resolve().parents[2] / ".basicly" / '
        '"kit" / "tracker" / "locate.py"',
    )
    folder = repo / ".git" / "hooks"
    (folder / "engine.py").write_text(text, encoding="utf-8")
    hook = folder / "commit-msg"
    hook.write_text(
        f'#!/bin/sh\nexec "{Path(sys.executable).as_posix()}" '
        '"$(git rev-parse --git-path hooks)/engine.py" "$@"\n',
        encoding="utf-8",
    )
    hook.chmod(hook.stat().st_mode | 0o111)


@pytest.mark.parametrize("gate", ("standalone", "engine"))
def test_an_unstaged_claim_cannot_authorize_a_code_commit(repo: Path, gate: str) -> None:
    _git(repo, "add", "-A")
    assert _git(repo, "commit", "-q", "-m", "chore: install tracker").returncode == 0
    _use_gate(repo, gate)
    record = _kit(repo, "create", ".basicly/ledger", "--prefix", "acme", "--title", "x", *SHAPED)[
        "record"
    ]
    _git(repo, "add", ".basicly/ledger")
    assert _git(repo, "commit", "-q", "-m", f"chore: file {record}").returncode == 0
    _kit(repo, "claim", ".basicly/ledger", record)
    (repo / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(repo, "add", "app.py")
    refused = _git(repo, "commit", "-q", "-m", f"feat: value {record}")
    assert refused.returncode != 0
    assert "Claim the record first" in refused.stderr
    _git(repo, "add", ".basicly/ledger")
    assert _git(repo, "commit", "-q", "-m", f"feat: value {record}").returncode == 0


@pytest.mark.parametrize("gate", ("standalone", "engine"))
def test_a_new_unstaged_record_cannot_authorize_the_first_code_commit(
    repo: Path, gate: str
) -> None:
    _git(repo, "add", "-A")
    assert _git(repo, "commit", "-q", "-m", "chore: install tracker").returncode == 0
    _use_gate(repo, gate)
    record = _kit(repo, "create", ".basicly/ledger", "--prefix", "acme", "--title", "x", *SHAPED)[
        "record"
    ]
    _kit(repo, "claim", ".basicly/ledger", record)
    (repo / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(repo, "add", "app.py")
    refused = _git(repo, "commit", "-q", "-m", f"feat: value {record}")
    assert refused.returncode != 0
    assert "git add .basicly/ledger" in refused.stderr


def test_claim_gate_targets_the_common_hooks_in_a_linked_worktree(
    repo: Path, tmp_path: Path
) -> None:
    _git(repo, "add", "-A")
    assert _git(repo, "commit", "-q", "-m", "chore: install tracker").returncode == 0
    record = _kit(
        repo, "create", ".basicly/ledger", "--prefix", "acme", "--title", "linked", *SHAPED
    )["record"]
    _git(repo, "add", ".basicly/ledger")
    assert _git(repo, "commit", "-q", "-m", f"chore: file {record}").returncode == 0
    lane = tmp_path / "lane"
    assert _git(repo, "worktree", "add", "-b", "lane", str(lane)).returncode == 0
    assert _hook(lane).returncode == 0
    hook_path = _git(lane, "rev-parse", "--git-path", "hooks/commit-msg").stdout.strip()
    assert "basicly-tracker claim" in Path(hook_path).read_text("utf-8")
    (lane / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(lane, "add", "app.py")
    refused = _git(lane, "commit", "-q", "-m", f"feat: linked value {record}")
    assert refused.returncode != 0 and "Claim the record first" in refused.stderr
    _kit(lane, "claim", ".basicly/ledger", record)
    _git(lane, "add", ".basicly/ledger")
    assert _git(lane, "commit", "-q", "-m", f"feat: linked value {record}").returncode == 0


def test_redirected_working_ledger_cannot_override_the_lane_index(
    repo: Path, tmp_path: Path
) -> None:
    _git(repo, "add", "-A")
    assert _git(repo, "commit", "-q", "-m", "chore: install tracker").returncode == 0
    record = _kit(
        repo, "create", ".basicly/ledger", "--prefix", "acme", "--title", "redirect", *SHAPED
    )["record"]
    _kit(repo, "claim", ".basicly/ledger", record)
    _git(repo, "add", ".basicly/ledger")
    assert _git(repo, "commit", "-q", "-m", f"chore: publish claim {record}").returncode == 0
    lane = tmp_path / "redirected-lane"
    assert _git(repo, "worktree", "add", "-b", "redirected-lane", str(lane)).returncode == 0
    (lane / ".basicly/ledger/redirect").write_text(str(repo), encoding="utf-8")
    (lane / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(lane, "add", "app.py")
    assert _git(lane, "commit", "-q", "-m", f"feat: published value {record}").returncode == 0
    _kit(repo, "unassign", ".basicly/ledger", record)
    (lane / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
    _git(lane, "add", "app.py")
    assert _git(lane, "commit", "-q", "-m", f"feat: staged claim value {record}").returncode == 0


def test_an_unreadable_staged_ledger_cannot_authorize_code(repo: Path) -> None:
    _git(repo, "add", "-A")
    assert _git(repo, "commit", "-q", "-m", "chore: install tracker").returncode == 0
    record = _kit(repo, "create", ".basicly/ledger", "--prefix", "acme", "--title", "x", *SHAPED)[
        "record"
    ]
    _kit(repo, "claim", ".basicly/ledger", record)
    log = next((repo / ".basicly/ledger").glob("pending-*.jsonl"))
    log.write_text(log.read_text("utf-8") + "not an event\n", encoding="utf-8")
    (repo / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(repo, "add", "-A")
    refused = _git(repo, "commit", "-q", "-m", f"feat: value {record}")
    assert refused.returncode != 0
    assert "staged ledger .basicly/ledger has unreadable events" in refused.stderr


@pytest.mark.parametrize("gate", ("standalone", "engine"))
@pytest.mark.parametrize("symlinks", ("true", "false"))
def test_a_staged_ledger_symlink_cannot_authorize_code(
    repo: Path, tmp_path: Path, gate: str, symlinks: str
) -> None:
    _git(repo, "add", "-A")
    assert _git(repo, "commit", "-q", "-m", "chore: install tracker").returncode == 0
    _use_gate(repo, gate)
    record = _kit(repo, "create", ".basicly/ledger", "--prefix", "acme", "--title", "x", *SHAPED)[
        "record"
    ]
    _kit(repo, "claim", ".basicly/ledger", record)
    log = next((repo / ".basicly/ledger").glob("pending-*.jsonl"))
    outside = tmp_path / "outside.jsonl"
    outside.write_text(log.read_text("utf-8"), encoding="utf-8")
    target = tmp_path / "target"
    target.write_text(str(outside), encoding="utf-8")
    blob = _git(repo, "hash-object", "-w", str(target)).stdout.strip()
    (repo / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(repo, "add", "-A")
    indexed = _git(
        repo, "update-index", "--cacheinfo", f"120000,{blob},{log.relative_to(repo).as_posix()}"
    )
    assert indexed.returncode == 0, indexed.stderr
    _git(repo, "config", "core.symlinks", symlinks)
    refused = _git(repo, "commit", "-q", "-m", f"feat: value {record}")
    assert refused.returncode != 0
    assert "staged ledger" in refused.stderr and "symlink" in refused.stderr
