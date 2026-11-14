from __future__ import annotations

import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from basicly import landing_guard, merge
from tests.test_kit_ledger_freeze import freeze
from tests.test_kit_vault_cli_wiring import LEDGER, _environment, _kit, _repo
from tests.test_kit_vault_share import bare, vault_queue
from tests.test_kit_vault_view import _carry, _push, _record

__all__ = ["_environment", "bare"]
ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / ".basicly/core/hooks"


def _frozen(tmp_path: Path, bare: Path) -> Path:
    root = _repo(tmp_path, bare)
    subprocess.run(["git", "-C", str(root), "config", "basicly.holder", "test-holder"], check=True)
    ledger = root / LEDGER
    freeze.freeze(ledger, str(bare), "acme", freeze.fingerprint(ledger))
    shutil.copytree(
        ROOT / ".basicly/core/kit/tracker",
        root / ".basicly/core/kit/tracker",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    (root / "pyproject.toml").write_text("[tool.ledger_fsck.frozen]\n", encoding="utf-8")
    return root


def _run(root: Path, entry: str, message: str) -> subprocess.CompletedProcess[str]:
    note = root / "MSG"
    note.write_text(message, encoding="utf-8")
    if entry == "kit":
        return _kit(root, "commit-check", LEDGER, str(note))
    return subprocess.run(
        [sys.executable, str(HOOKS / "tracker-claim.py"), str(note)],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )


def _history(root: Path) -> dict[Path, bytes]:
    archive = root / ".basicly/ledger-archive"
    return {
        Path(LEDGER): (root / LEDGER).read_bytes(),
        **{p.relative_to(root): p.read_bytes() for p in archive.rglob("*") if p.is_file()},
    }


@pytest.mark.parametrize("entry", ["kit", "harness"])
def test_frozen_commit_without_a_record_uses_native_claim_refusal(
    tmp_path: Path, bare: Path, entry: str
) -> None:
    root = _frozen(tmp_path, bare)
    result = _run(root, entry, "fix: preserve the queue\n")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "claim RECORD" in result.stdout + result.stderr
    assert "Record: none" in result.stdout + result.stderr


@pytest.mark.parametrize("entry", ["kit", "harness"])
def test_named_unheld_record_is_advisory_and_preserves_frozen_history(
    tmp_path: Path, bare: Path, entry: str
) -> None:
    root = _frozen(tmp_path, bare)
    before = _history(root)
    result = _run(root, entry, "fix: preserve the queue (acme-a1b2)\n")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "claim" in result.stdout + result.stderr
    assert _history(root) == before


@pytest.mark.parametrize("entry", ["kit", "harness"])
def test_one_settled_claim_fills_the_record_in_the_actual_message(
    tmp_path: Path, bare: Path, entry: str
) -> None:
    root = _frozen(tmp_path, bare)
    claimed = _kit(root, "claim", "acme-a1b2")
    assert claimed.returncode == 0, claimed.stdout + claimed.stderr
    result = _run(root, entry, "fix: preserve the queue\n\nKeep this body.\n")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (root / "MSG").read_text(
        encoding="utf-8"
    ) == "fix: preserve the queue (acme-a1b2)\n\nKeep this body.\n"


def _surface(root: Path, entry: str) -> subprocess.CompletedProcess[str]:
    if entry == "fsck":
        command = [sys.executable, str(ROOT / ".scripts/check_ledger_fsck.py"), "--repo", str(root)]
    else:
        command = [sys.executable, str(HOOKS / f"tracker-{entry}.py")]
    return subprocess.run(command, cwd=root, capture_output=True, text=True, check=False)


@pytest.mark.parametrize("entry", ["path-scan", "fold", "fsck"])
def test_frozen_surfaces_read_canonical_records_without_changing_the_archive(
    tmp_path: Path, bare: Path, entry: str
) -> None:
    root = _frozen(tmp_path, bare)
    _push(tmp_path, bare, "publisher", _carry(_record("acme-c3d4")))
    before = (root / LEDGER).read_bytes()
    result = _surface(root, entry)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (root / LEDGER).read_bytes() == before
    if entry == "fsck":
        assert "2 event(s) over 1 record(s)" in result.stdout
    assert not list((root / ".basicly/ledger-archive").glob("*.jsonl"))


def test_path_scan_catches_an_unredacted_canonical_path_outside_local_git(
    tmp_path: Path, bare: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _frozen(tmp_path, bare)
    with monkeypatch.context() as unsafe_producer:
        unsafe_producer.setattr(vault_queue.redact, "event", lambda event: event)
        _push(
            tmp_path,
            bare,
            "publisher",
            _carry(_record("acme-c3d4", title="Inspect /home/private-agent/demo")),
        )
    result = _surface(root, "path-scan")
    assert result.returncode == 1
    assert "posix-home-path" in result.stderr
    assert "preserve the frozen ledger" in result.stderr
    assert "private-agent" not in result.stderr


@pytest.mark.parametrize("entry", ["path-scan", "fold", "fsck", "harness", "kit"])
def test_all_surfaces_refuse_an_archive_changed_by_an_old_tool(
    tmp_path: Path, bare: Path, entry: str
) -> None:
    root = _frozen(tmp_path, bare)
    shard = root / ".basicly/ledger-archive/pending-old.jsonl"
    shard.write_text('{"record":"acme-a1b2"}\n', encoding="utf-8")
    result = (
        _run(root, entry, "fix: inspect (acme-a1b2)\n")
        if entry in {"kit", "harness"}
        else _surface(root, entry)
    )
    assert result.returncode == (2 if entry == "kit" else 1), result.stdout + result.stderr
    assert "archive" in result.stdout + result.stderr
    assert shard.read_text(encoding="utf-8") == '{"record":"acme-a1b2"}\n'


def test_merge_reads_vault_ids_and_never_folds_or_commits_local_tracker_data(
    tmp_path: Path, bare: Path
) -> None:
    root = _frozen(tmp_path, bare)
    _push(tmp_path, bare, "publisher", _carry(_record("acme-c3d4")))
    before = (root / LEDGER).read_bytes()
    assert merge.known_bead_ids(root) == {"acme-c3d4"}
    assert merge.commit_tracker_state(root, "acme-c3d4") is False
    assert (root / LEDGER).read_bytes() == before
    assert not list((root / ".basicly/ledger-archive").glob("*.jsonl"))


def test_an_empty_vault_has_an_empty_known_id_set_instead_of_disabling_the_check(
    tmp_path: Path, bare: Path
) -> None:
    root = _frozen(tmp_path, bare)
    assert merge.known_bead_ids(root) == set()


def test_landing_refuses_a_lane_that_changes_frozen_history_before_any_merge(
    tmp_path: Path, bare: Path
) -> None:
    root = _frozen(tmp_path, bare)

    def git(*args: str) -> str:
        return subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "-c",
                "user.name=test-holder",
                "-c",
                "user.email=test@example.invalid",
                *args,
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    git("add", ".basicly", ".basicly-tracker.toml", "pyproject.toml")
    git("commit", "-qm", "seed")
    git("branch", "-M", "main")
    git("checkout", "-qb", "candidate")
    (root / "source.txt").write_text("ordinary code\n", encoding="utf-8")
    git("add", "source.txt")
    git("commit", "-qm", "ordinary change")
    git("checkout", "-q", "main")
    assert landing_guard.frozen_tracker_change(root, "main", "candidate") == ""
    before = _history(root)
    destination = git("rev-parse", "main")
    git("checkout", "-q", "candidate")
    (root / ".basicly/ledger-archive/pending-topic.jsonl").write_text(
        '{"record":"acme-c3d4"}\n', encoding="utf-8"
    )
    git("add", ".basicly/ledger-archive")
    git("commit", "-qm", "late legacy event")
    git("checkout", "-q", "main")
    reason = landing_guard.frozen_tracker_change(root, "main", "candidate")
    assert "frozen tracker data" in reason and "--to-vault --preview" in reason
    assert _history(root) == before
    assert git("rev-parse", "main") == destination


@pytest.mark.parametrize("entry", ["kit", "harness"])
@pytest.mark.parametrize(
    "message",
    [
        "docs: spelling\n\nRecord: none\n",
        "Merge branch 'topic' into main\n",
        'Revert "fix: queue (acme-a1b2)"\n',
        "fixup! fix: queue (acme-a1b2)\n",
        "squash! fix: queue (acme-a1b2)\n",
    ],
)
def test_native_exempt_message_forms_are_preserved(
    tmp_path: Path, bare: Path, entry: str, message: str
) -> None:
    root = _frozen(tmp_path, bare)
    result = _run(root, entry, message)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (root / "MSG").read_text(encoding="utf-8") == message


@pytest.mark.parametrize("entry", ["standalone", "harness"])
def test_real_git_commit_uses_native_claims_once_and_preserves_history(
    tmp_path: Path, bare: Path, entry: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _frozen(tmp_path, bare)
    if entry == "standalone":
        installed = subprocess.run(
            [
                sys.executable,
                str(root / ".basicly/core/kit/tracker/install_hook.py"),
                "--root",
                str(root),
                "--ledger",
                LEDGER,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert installed.returncode == 0, installed.stdout + installed.stderr
    else:
        commands = [
            shlex.join([Path(sys.executable).as_posix(), (HOOKS / f"tracker-{hook}.py").as_posix()])
            for hook in ("commit-msg", "claim")
        ]
        hook = root / ".git/hooks/commit-msg"
        hook.write_text(
            f'#!/bin/sh\n{commands[0]} "$@" || exit 1\nexec {commands[1]} "$@"\n', encoding="utf-8"
        )
        hook.chmod(hook.stat().st_mode | 0o111)

    def git(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "-c",
                "user.name=test-holder",
                "-c",
                "user.email=test@example.invalid",
                *args,
            ],
            capture_output=True,
            text=True,
            check=False,
        )

    before = _history(root)
    source = root / "source.txt"
    source.write_text("first\n", encoding="utf-8")
    assert git("add", "source.txt").returncode == 0
    refused = git("commit", "-qm", "fix: preserve the queue")
    assert refused.returncode != 0 and "Record: none" in refused.stderr
    accepted = git("commit", "-qm", "fix: preserve the queue (acme-a1b2)")
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    assert accepted.stderr.count("this session does not hold") == 1
    claimed = _kit(root, "claim", "acme-a1b2")
    assert claimed.returncode == 0, claimed.stdout + claimed.stderr
    source.write_text("second\n", encoding="utf-8")
    assert git("add", "source.txt").returncode == 0
    filled = git("commit", "-qm", "fix: preserve the queue")
    assert filled.returncode == 0, filled.stdout + filled.stderr
    assert git("log", "-1", "--format=%s").stdout.strip() == "fix: preserve the queue (acme-a1b2)"
    monkeypatch.setenv("BASICLY_TRACKER_SESSION", "another-session")
    source.write_text("third\n", encoding="utf-8")
    assert git("add", "source.txt").returncode == 0
    advisory = git("commit", "-qm", "fix: preserve the queue (acme-a1b2)")
    assert advisory.returncode == 0, advisory.stdout + advisory.stderr
    assert advisory.stderr.count("this session does not hold") == 1
    assert _history(root) == before
