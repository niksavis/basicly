from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from types import ModuleType

import pytest

from tests.test_kit_vault_shapes import Vault

REPO_ROOT = Path(__file__).resolve().parents[1]
KIT = REPO_ROOT / ".basicly" / "core" / "kit" / "tracker"


def _load(name: str, file_name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, KIT / file_name)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


vault_queue = _load("basicly_tracker_kit_vault_queue", "vault_queue.py")
cli = _load("tracker_cli_vault_queue", "cli.py")

TOKEN = "glpat" + "-" + "Zq9x" * 6
HOME = "/" + "home" + "/someone/secret-project/plan.md"


@pytest.fixture
def origin(tmp_path: Path) -> Vault:
    made = Vault(tmp_path / "origin")
    made.genesis()
    return made


@pytest.fixture(autouse=True)
def _slot_never_due(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sys.modules["basicly_tracker_kit_vault_hooks"], "slot_due", lambda *_: False
    )


@pytest.fixture(autouse=True)
def _no_ci(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("CI", "BASICLY_TRACKER_EPHEMERAL", "BASICLY_TRACKER_SESSION"):
        monkeypatch.delenv(name, raising=False)


def _comment(
    tmp_path: Path, origin: Vault, text: str, capsys: pytest.CaptureFixture[str]
) -> tuple[int, dict]:
    argv = ["comment", "acme-a1b2", "--vault", str(origin.root), "--text", text]
    code = cli.main([*argv, "--state", str(tmp_path / "state")])
    return code, json.loads(capsys.readouterr().out)


def _queue(tmp_path: Path, origin: Vault) -> Path:
    return (
        vault_queue.vault_clone.locate(tmp_path / "state", str(origin.root)).folder / "queue.jsonl"
    )


def test_proof_t_redact_a_token_and_a_home_path_never_reach_the_queue(
    tmp_path: Path, origin: Vault, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _shown = _comment(tmp_path, origin, f"see {HOME} with {TOKEN}", capsys)
    stored = _queue(tmp_path, origin).read_text(encoding="utf-8")

    assert code == cli.EXIT_OK
    assert TOKEN not in stored
    assert HOME not in stored
    assert "<redacted:gitlab-token>" in stored
    assert "<redacted:posix-home-path>" in stored


def test_a_queued_write_prints_the_count_the_oldest_age_and_who_shares_it(
    tmp_path: Path, origin: Vault, capsys: pytest.CaptureFixture[str]
) -> None:
    _comment(tmp_path, origin, "first", capsys)
    code, shown = _comment(tmp_path, origin, "second", capsys)
    lines = _queue(tmp_path, origin).read_text(encoding="utf-8").splitlines()

    assert code == cli.EXIT_OK
    assert shown["queued"] == 1
    assert shown["pending"] == 2
    assert shown["oldest_age_s"] >= 0
    assert "Stop" in shown["share"]
    assert [json.loads(line)["kind"] for line in lines] == ["comment", "comment"]
    assert len({json.loads(line)["writer"] for line in lines}) == 1


@pytest.mark.parametrize("name", ["CI", "BASICLY_TRACKER_EPHEMERAL"])
def test_ci_or_an_ephemeral_session_requests_an_immediate_share(
    tmp_path: Path,
    origin: Vault,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    name: str,
) -> None:
    monkeypatch.setenv(name, "1")

    _code, shown = _comment(tmp_path, origin, "now", capsys)

    assert shown["share"] == "immediate"


def test_a_torn_tail_is_cut_back_and_reported(
    tmp_path: Path, origin: Vault, capsys: pytest.CaptureFixture[str]
) -> None:
    _comment(tmp_path, origin, "kept", capsys)
    queue = _queue(tmp_path, origin)
    with queue.open("a", encoding="utf-8") as stream:
        stream.write('{"kind": "comment", "record": "lost')

    _code, shown = _comment(tmp_path, origin, "after", capsys)
    lines = queue.read_text(encoding="utf-8").splitlines()

    assert shown["torn"]["bytes"] > 0
    assert shown["torn"]["start"].startswith('{"kind": "comment"')
    assert [json.loads(line)["payload"]["text"] for line in lines] == ["kept", "after"]


def test_the_writer_id_is_kept_private_and_reused(tmp_path: Path) -> None:
    first = vault_queue.writer_of(tmp_path / "state")

    assert vault_queue.writer_of(tmp_path / "state") == first
    if os.name != "nt":
        assert (tmp_path / "state" / "writer.id").stat().st_mode & 0o777 == 0o600


def test_an_empty_comment_or_a_bad_record_id_is_refused_by_name(
    tmp_path: Path, origin: Vault, capsys: pytest.CaptureFixture[str]
) -> None:
    code, shown = _comment(tmp_path, origin, "  ", capsys)

    assert code == cli.EXIT_REFUSED
    assert "--text" in shown["refused"]
    with pytest.raises(vault_queue.QueueError, match="record id"):
        vault_queue.comment("bad\nid", "x")


def test_a_comment_above_the_byte_cap_after_redaction_is_refused(
    tmp_path: Path, origin: Vault, capsys: pytest.CaptureFixture[str]
) -> None:
    code, shown = _comment(tmp_path, origin, "x" * 5000, capsys)

    assert code == cli.EXIT_REFUSED
    assert "4096 bytes" in shown["refused"]
    assert not _queue(tmp_path, origin).exists()


def test_comment_without_vault_still_writes_the_ledger(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = cli.main(["comment", str(tmp_path / "ledger"), "acme-a1b2", "hello"])
    shown = json.loads(capsys.readouterr().out)

    assert shown["schema"].startswith("basicly.tracker.comment.")
    assert code == cli.EXIT_REFUSED or "appended" in shown
