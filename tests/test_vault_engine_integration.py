from __future__ import annotations

from pathlib import Path

import pytest

from basicly import cli, owned_store, owned_write
from tests.test_kit_vault_cli_wiring import LEDGER, _environment, _kit, _report
from tests.test_kit_vault_lifecycle import _create, _run
from tests.test_vault_harness_hooks import _frozen, _history, bare

__all__ = ["_environment", "bare"]


def test_engine_tracker_setup_preserves_a_valid_frozen_binding(tmp_path: Path, bare: Path) -> None:
    root = _frozen(tmp_path, bare)
    before = _history(root)
    config = root / ".basicly-tracker.toml"
    original = config.read_bytes()

    cli._setup_tracker(root)

    assert _history(root) == before
    assert config.read_bytes() == original


@pytest.mark.parametrize("immediate", [False, True])
def test_actual_engine_receipt_observes_the_pending_or_published_vault_event(
    tmp_path: Path,
    bare: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    immediate: bool,
) -> None:
    root = _frozen(tmp_path, bare)
    record = _create(root, "A canonical engine receipt")
    if immediate:
        monkeypatch.setenv("BASICLY_TRACKER_EPHEMERAL", "1")
    monkeypatch.chdir(root)
    before = _history(root)

    code = cli.main(["tracker", "write", "--", "comments", "add", record, "One accepted note"])

    output = capsys.readouterr()
    assert code == 0, output.out + output.err
    shown = _run(root, "show", LEDGER, record)
    assert len([row for row in shown["comment_log"] if row["text"] == "One accepted note"]) == 1
    assert _history(root) == before


def test_a_missing_vault_event_still_refuses_an_engine_receipt(
    tmp_path: Path,
    bare: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = _frozen(tmp_path, bare)
    record = _create(root, "A missing engine event")
    model = owned_store.kit(root)
    payload = {"text": "The store did not keep this note"}
    fake = model.events.Event(
        model.events.event_id_for(record, "comment", payload),
        record,
        1,
        "comment",
        "fixture",
        "2026-11-14T00:00:00Z",
        payload,
    )
    monkeypatch.setattr(owned_write, "append", lambda *_a, **_k: ([fake], [fake]))
    monkeypatch.chdir(root)
    before = _history(root)

    code = cli.main(["tracker", "write", "--", "comments", "add", record, payload["text"]])

    output = capsys.readouterr()
    assert code == 1, output.out + output.err
    assert "does not hold what it just accepted" in output.err
    assert _history(root) == before
    shown = _report(_kit(root, "show", LEDGER, record))
    assert not any(row["text"] == payload["text"] for row in shown.get("comment_log", []))
