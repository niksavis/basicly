from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import TYPE_CHECKING

from tests.test_kit_vault_cli_wiring import _environment, vault_clone
from tests.test_kit_vault_share import bare, vault_queue
from tests.test_kit_vault_view import _carry, _push, _record
from tests.test_vault_harness_hooks import HOOKS, _frozen, _surface

if TYPE_CHECKING:
    import pytest

__all__ = ["_environment", "bare"]


def _publish_unredacted(tmp_path: Path, bare: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with monkeypatch.context() as unsafe_producer:
        unsafe_producer.setattr(vault_queue.redact, "event", lambda event: event)
        _push(
            tmp_path,
            bare,
            "publisher",
            _carry(_record("acme-c3d4", title="Inspect /home/private-agent/demo")),
        )


def _queue(tmp_path: Path, bare: Path, line: str) -> Path:
    folder = vault_clone.locate(tmp_path / "state", str(bare)).folder
    folder.mkdir(parents=True, exist_ok=True)
    queue = folder / vault_queue.QUEUE_NAME
    queue.write_text(line + "\n", encoding="utf-8")
    return queue


QUEUED = (
    '{"id": "acme-c3d4#ev-0000000001", "kind": "note", "record": "acme-c3d4", '
    '"payload": {"text": "Inspect /home/private-agent/demo"}}'
)


def test_path_scan_does_not_block_on_a_published_event(
    tmp_path: Path, bare: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _frozen(tmp_path, bare)
    _publish_unredacted(tmp_path, bare, monkeypatch)
    result = _surface(root, "path-scan")
    assert result.returncode == 0, result.stderr


def test_path_scan_refuses_a_queued_event_with_a_home_path(tmp_path: Path, bare: Path) -> None:
    root = _frozen(tmp_path, bare)
    _queue(tmp_path, bare, QUEUED)
    result = _surface(root, "path-scan")
    assert result.returncode == 1
    assert "vault/queue.jsonl:1: posix-home-path" in result.stderr
    assert "preserve the frozen ledger" in result.stderr
    assert "private-agent" not in result.stderr


def test_path_scan_refuses_a_queued_event_on_a_published_record(
    tmp_path: Path, bare: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _frozen(tmp_path, bare)
    _publish_unredacted(tmp_path, bare, monkeypatch)
    _queue(tmp_path, bare, QUEUED)
    result = _surface(root, "path-scan")
    assert result.returncode == 1
    assert "vault/queue.jsonl:1: posix-home-path" in result.stderr


def test_path_scan_falls_back_to_the_full_view_when_the_queue_is_unreadable(
    tmp_path: Path, bare: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _frozen(tmp_path, bare)
    _publish_unredacted(tmp_path, bare, monkeypatch)
    queue = _queue(tmp_path, bare, "")
    queue.unlink()
    queue.mkdir()
    result = _surface(root, "path-scan")
    assert result.returncode == 1
    assert "tracker-path-scan:" in result.stderr


def test_path_scan_refuses_a_queued_event_that_names_the_user(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = importlib.util.spec_from_file_location("scan_user_test", HOOKS / "tracker-path-scan.py")
    assert spec is not None
    assert spec.loader is not None
    scan = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scan)
    monkeypatch.setattr(scan.getpass, "getuser", lambda: "agent-tester")
    monkeypatch.setattr(scan, "configured_holder", lambda: "")
    line = '{"id": "acme-c3d4#ev-1", "kind": "note", "payload": {"text": "held by agent-tester"}}'
    monkeypatch.setattr(scan, "vault_queue_text", lambda: line + "\n")
    assert scan.vault_findings(tmp_path) == [("vault/queue.jsonl", 1, "machine-username")]


def test_path_scan_scans_the_view_when_the_queue_reader_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = importlib.util.spec_from_file_location("scan_under_test", HOOKS / "tracker-path-scan.py")
    assert spec is not None
    assert spec.loader is not None
    scan = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scan)
    view = tmp_path / "view"
    view.mkdir()
    (view / "events-0001.jsonl").write_text(QUEUED + "\n", encoding="utf-8")

    def unreadable() -> str:
        raise OSError("queue unreadable")

    monkeypatch.setattr(scan, "vault_queue_text", unreadable)
    assert scan.vault_findings(view) == [("vault/events-0001.jsonl", 1, "posix-home-path")]
