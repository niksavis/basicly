from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.test_kit_vault_init import _code_repo, _empty, _git, _run, cli
from tests.test_tracker_generation import KIT, _legacy_kit


def test_fresh_vault_init_blocks_an_independent_old_tool_before_any_local_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    old = tmp_path / "old-tool"
    _legacy_kit(old)
    command = [
        sys.executable,
        str(old / KIT / "cli.py"),
        "create",
        ".basicly/ledger",
        "--prefix",
        "acme",
        "--title",
        "An older writer",
    ]
    control = tmp_path / "plain-control"
    control.mkdir()
    allowed = subprocess.run(command, cwd=control, capture_output=True, text=True, check=False)
    assert allowed.returncode == 0, allowed.stdout + allowed.stderr
    bare, root = _empty(tmp_path), _code_repo(tmp_path)
    monkeypatch.chdir(root)
    monkeypatch.setenv("BASICLY_TRACKER_STATE", str(tmp_path / "state"))
    monkeypatch.setenv("BASICLY_TRACKER_SESSION", "fresh-init")
    code, shown = _run(["vault-init", "--vault", str(bare), "--yes", "--prefix", "acme"], capsys)
    assert code == 0, shown

    refused = subprocess.run(
        command, cwd=root, env=dict(os.environ), capture_output=True, text=True, check=False
    )

    assert refused.returncode != 0, refused.stdout + refused.stderr
    ledger = root / ".basicly/ledger"
    assert ledger.is_file()
    assert cli.vault_cli.ledger_freeze.check(ledger) == ledger.with_name("ledger-archive")
    assert not list(ledger.with_name("ledger-archive").glob("*.jsonl"))


def test_init_with_local_history_refuses_before_genesis_or_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    bare, root = _empty(tmp_path), _code_repo(tmp_path)
    monkeypatch.chdir(root)
    code, shown = _run(
        ["create", ".basicly/ledger", "--prefix", "acme", "--title", "Existing history"], capsys
    )
    assert code == 0, shown
    ledger = root / ".basicly/ledger"
    before = {path.name: path.read_bytes() for path in ledger.iterdir() if path.is_file()}

    code, shown = _run(["vault-init", "--vault", str(bare), "--yes", "--prefix", "acme"], capsys)

    assert code != 0, shown
    assert "migrate" in shown["refused"]
    assert _git("ls-remote", str(bare), cwd=tmp_path) == ""
    assert not (root / ".basicly-tracker.toml").exists()
    assert {path.name: path.read_bytes() for path in ledger.iterdir() if path.is_file()} == before


def test_init_validates_the_project_prefix_before_pushing_a_genesis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    bare, root = _empty(tmp_path), _code_repo(tmp_path)
    monkeypatch.chdir(root)

    code, shown = _run(["vault-init", "--vault", str(bare), "--yes", "--prefix", "Invalid"], capsys)

    assert code != 0, shown
    assert "prefix" in shown["refused"]
    assert _git("ls-remote", str(bare), cwd=tmp_path) == ""


def test_a_fresh_clone_keeps_the_empty_archive_and_can_read_its_vault(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    bare, root = _empty(tmp_path), _code_repo(tmp_path)
    monkeypatch.chdir(root)
    monkeypatch.setenv("BASICLY_TRACKER_STATE", str(tmp_path / "state"))
    code, shown = _run(["vault-init", "--vault", str(bare), "--yes", "--prefix", "acme"], capsys)
    assert code == 0, shown
    _git("add", ".basicly", ".basicly-tracker.toml", cwd=root)
    _git(
        "-c",
        "user.name=fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-qm",
        "Initialize the vault consumer",
        cwd=root,
    )
    clone = tmp_path / "fresh-clone"
    _git("clone", "-q", str(root), str(clone), cwd=tmp_path)
    monkeypatch.chdir(clone)

    code, shown = _run(["list", ".basicly/ledger"], capsys)

    assert code == 0, shown
    assert shown["records"] == []
    assert (clone / ".basicly/ledger-archive").is_dir()


@pytest.mark.parametrize("existing", ["config", "redirect", "template", "archive"])
def test_init_refuses_existing_project_state_before_publishing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    existing: str,
) -> None:
    bare, root = _empty(tmp_path), _code_repo(tmp_path)
    ledger = root / ".basicly/ledger"
    ledger.mkdir(parents=True)
    if existing == "config":
        path = root / ".basicly-tracker.toml"
        path.write_text(
            cli.vault_config.text(str(tmp_path / "original.git"), "acme"), encoding="utf-8"
        )
    elif existing == "redirect":
        path = ledger / "redirect"
        path.write_text(str(tmp_path / "base"), encoding="utf-8")
    elif existing == "template":
        path = ledger / "template.json"
        path.write_text('{"stale_days":21}', encoding="utf-8")
    else:
        path = ledger.with_name("ledger-archive")
        path.mkdir()
    before = {
        path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()
    }
    monkeypatch.chdir(root)

    code, shown = _run(["vault-init", "--vault", str(bare), "--yes", "--prefix", "acme"], capsys)

    assert code != 0, shown
    assert _git("ls-remote", str(bare), cwd=tmp_path) == ""
    assert {path: (root / path).read_bytes() for path in before} == before


@pytest.mark.parametrize("context", ["outside", "worktree", "invalid"])
def test_prefix_attachment_requires_the_base_git_checkout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    context: str,
) -> None:
    bare = _empty(tmp_path)
    if context in {"outside", "invalid"}:
        root = tmp_path / "outside"
        root.mkdir()
        if context == "invalid":
            (root / ".git").mkdir()
            (root / ".git/HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    else:
        base = _code_repo(tmp_path)
        _git(
            "-c",
            "user.name=fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "--allow-empty",
            "-qm",
            "Initial commit",
            cwd=base,
        )
        root = tmp_path / "lane"
        _git("worktree", "add", "-q", "-b", "lane", str(root), cwd=base)
    monkeypatch.chdir(root)

    code, shown = _run(["vault-init", "--vault", str(bare), "--yes", "--prefix", "acme"], capsys)

    assert code != 0, shown
    assert _git("ls-remote", str(bare), cwd=tmp_path) == ""
    assert not (root / ".basicly-tracker.toml").exists()


@pytest.mark.parametrize("competing_writer", [False, True])
def test_failed_guard_installation_never_reports_config_only_attachment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    competing_writer: bool,
) -> None:
    bare, root = _empty(tmp_path), _code_repo(tmp_path)
    ledger = root / ".basicly/ledger"
    monkeypatch.chdir(root)
    original = Path.open

    def interrupt(path: Path, *args: Any, **kwargs: Any) -> Any:
        if path == ledger and args and args[0] == "x":
            if competing_writer:
                cli.events.append(
                    ledger, [cli.events.Draft("acme-a1b2", "created", {"title": "Concurrent work"})]
                )
            else:
                raise PermissionError("simulated guard creation refusal")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", interrupt)

    code, shown = _run(["vault-init", "--vault", str(bare), "--yes", "--prefix", "acme"], capsys)

    assert code == 2, shown
    assert shown["pushed"] is True
    assert shown["refused"]
    assert not (root / ".basicly-tracker.toml").exists()
    assert not ledger.with_name("ledger-archive").exists()
    if competing_writer:
        found, bad = cli.events.read_events(ledger)
        assert not bad
        assert cli.events.fold(found).records["acme-a1b2"].fields["title"] == "Concurrent work"
    else:
        assert not ledger.exists()
