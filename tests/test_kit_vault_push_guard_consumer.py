from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_kit_vault_push_guard import _event
from tests.test_kit_vault_shapes import KIT, WRITER_A, Vault, _segment


def _load(name: str):
    key = "basicly_tracker_kit_" + name
    spec = importlib.util.spec_from_file_location(key, KIT / (name + ".py"))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[key] = module
    spec.loader.exec_module(module)
    return module


def _clone(tmp_path: Path) -> tuple[Vault, Path]:
    source = Vault(tmp_path / "source")
    for name, content in _load("vault_init").genesis_files().items():
        source.write(name, content)
    source.commit("genesis")
    remote = tmp_path / "remote.git"
    source.git("clone", "--bare", "-q", str(source.root), str(remote))
    source.git("remote", "add", "origin", str(remote))
    return source, remote


def _push(source: Vault, *refspecs: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "push", "origin", *refspecs],
        cwd=source.root,
        env=source.env,
        capture_output=True,
        text=True,
        check=False,
    )


def _head(remote: Path) -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "refs/heads/main"], cwd=remote, text=True
    ).strip()


def exercise_vault_push_guard(tmp_path: Path) -> None:
    tmp_path.mkdir(parents=True, exist_ok=True)
    source, remote = _clone(tmp_path)
    installer = _load("vault_push_hooks")
    assert installer.install(source.root)["protected"]
    source.write(f"log/{WRITER_A}/00001.jsonl", _segment(WRITER_A, _event()))
    accepted = source.commit("valid tracker batch")
    pushed = _push(source, "HEAD:refs/heads/main")
    assert pushed.returncode == 0, pushed.stderr
    assert _head(remote) == accepted
    source.write("application.py", "print('unrelated project')\n")
    source.commit("unrelated project code")
    refused = _push(source, "HEAD:refs/heads/main")
    assert refused.returncode != 0 and "vault-push" in refused.stderr
    assert _head(remote) == accepted


def test_installed_guard_accepts_tracker_events_and_rejects_unrelated_code(tmp_path: Path) -> None:
    exercise_vault_push_guard(tmp_path / "vault clone's files")


@pytest.mark.parametrize(
    "refspec", ["HEAD:refs/heads/extra", "HEAD:refs/tags/version", ":refs/heads/main"]
)
def test_installed_guard_refuses_extra_refs_or_deletion_without_remote_change(
    tmp_path: Path, refspec: str
) -> None:
    source, remote = _clone(tmp_path)
    _load("vault_push_hooks").install(source.root)
    before = _head(remote)
    refused = _push(source, refspec)
    assert refused.returncode != 0 and "vault-push" in refused.stderr
    assert _head(remote) == before
    refs = subprocess.check_output(
        ["git", "for-each-ref", "--format=%(refname)"], cwd=remote, text=True
    )
    assert refs.splitlines() == ["refs/heads/main"]


def test_install_preserves_an_existing_unrelated_hook_and_configuration(tmp_path: Path) -> None:
    source, _remote = _clone(tmp_path)
    hook = source.root / ".git/hooks/pre-push"
    original = b"#!/bin/sh\nexit 0\n"
    hook.write_bytes(original)
    config = source.root / ".git/config"
    before = config.read_bytes()
    with pytest.raises(ValueError, match="unrelated"):
        _load("vault_push_hooks").install(source.root)
    assert hook.read_bytes() == original
    assert config.read_bytes() == before
    assert not (source.root / ".git/basicly-vault-guard").exists()


def test_explicit_cli_protects_a_manual_vault_clone(tmp_path: Path) -> None:
    source, remote = _clone(tmp_path)
    done = subprocess.run(
        [sys.executable, str(KIT / "cli.py"), "vault-protect", "--repository", str(source.root)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    before = _head(remote)
    source.write("unexpected.txt", "Unrelated content.\n")
    source.commit("wrong content")
    refused = _push(source, "HEAD:refs/heads/main")
    assert refused.returncode != 0 and "vault-push" in refused.stderr
    assert _head(remote) == before


def test_install_is_idempotent_and_keeps_its_guard_usable(tmp_path: Path) -> None:
    source, _remote = _clone(tmp_path)
    installer = _load("vault_push_hooks")
    assert installer.install(source.root)["changed"]
    before = (source.root / ".git/hooks/pre-push").read_bytes()
    assert not installer.install(source.root)["changed"]
    assert (source.root / ".git/hooks/pre-push").read_bytes() == before


def test_explicit_protection_refuses_a_non_repository_without_a_traceback(tmp_path: Path) -> None:
    done = subprocess.run(
        [sys.executable, str(KIT / "cli.py"), "vault-protect", "--repository", str(tmp_path)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode != 0
    assert "--repository" in done.stdout and "Traceback" not in done.stderr


def test_one_forbidden_ref_blocks_every_update_in_the_actual_push(tmp_path: Path) -> None:
    source, remote = _clone(tmp_path)
    _load("vault_push_hooks").install(source.root)
    before = _head(remote)
    source.write(f"log/{WRITER_A}/00001.jsonl", _segment(WRITER_A, _event()))
    source.commit("valid batch with accidental extra branch")
    refused = _push(source, "HEAD:refs/heads/main", "HEAD:refs/heads/accidental")
    assert refused.returncode != 0 and "vault-push" in refused.stderr
    assert _head(remote) == before
    refs = subprocess.check_output(
        ["git", "for-each-ref", "--format=%(refname)"], cwd=remote, text=True
    )
    assert refs.splitlines() == ["refs/heads/main"]


@pytest.mark.parametrize("kind", ["claim", "take", "unassign"])
@pytest.mark.parametrize("session", ["", "named-session"])
def test_ownership_events_require_a_session_before_actual_publication(
    tmp_path: Path, kind: str, session: str
) -> None:
    source, remote = _clone(tmp_path)
    _load("vault_push_hooks").install(source.root)
    before = _head(remote)
    event = {**_event(), "kind": kind, "session": session, "payload": {"name": "fixture"}}
    source.write(f"log/{WRITER_A}/00001.jsonl", _segment(WRITER_A, event))
    tip = source.commit("ownership event")
    pushed = _push(source, "HEAD:refs/heads/main")
    if session:
        assert pushed.returncode == 0, pushed.stderr
        assert _head(remote) == tip
    else:
        assert pushed.returncode != 0 and "session" in pushed.stderr
        assert _head(remote) == before


def test_a_human_comment_keeps_its_supported_empty_session(tmp_path: Path) -> None:
    source, remote = _clone(tmp_path)
    _load("vault_push_hooks").install(source.root)
    source.write(f"log/{WRITER_A}/00001.jsonl", _segment(WRITER_A, {**_event(), "session": ""}))
    tip = source.commit("human comment")
    pushed = _push(source, "HEAD:refs/heads/main")
    assert pushed.returncode == 0, pushed.stderr
    assert _head(remote) == tip


@pytest.mark.parametrize("body_kind", ["empty", "wrong-record", "valid-human", "invalid-spend"])
def test_carried_ledger_bodies_use_the_existing_codec_and_match_the_envelope(
    tmp_path: Path, body_kind: str
) -> None:
    source, remote = _clone(tmp_path)
    _load("vault_push_hooks").install(source.root)
    before = _head(remote)
    body = {
        "id": "demo-a1-ledger-1234567890",
        "record": "demo-a1",
        "seq": 1,
        "kind": "created",
        "actor": "human",
        "ts": "2026-01-01T00:00:00Z",
        "payload": {"title": "Fixture"},
    }
    if body_kind == "empty":
        body = {}
    elif body_kind == "wrong-record":
        body["record"] = "other-a1"
    elif body_kind == "invalid-spend":
        body["payload"] = {"spend_micros": 1.5}
    event = {**_event(), "kind": "ledger", "session": "", "ledger": body}
    source.write(f"log/{WRITER_A}/00001.jsonl", _segment(WRITER_A, event))
    tip = source.commit("carried ledger event")
    pushed = _push(source, "HEAD:refs/heads/main")
    if body_kind == "valid-human":
        assert pushed.returncode == 0, pushed.stderr
        assert _head(remote) == tip
    else:
        assert pushed.returncode != 0 and "ledger" in pushed.stderr
        assert _head(remote) == before
