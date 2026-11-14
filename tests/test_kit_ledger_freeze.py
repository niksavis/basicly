from __future__ import annotations

import importlib.util
import json
import sys
import threading
from pathlib import Path
from typing import Any

import pytest

from tests.test_kit_vault_migrate import vault_migrate
from tests.test_kit_vault_share import bare
from tests.test_kit_vault_view import cli

__all__ = ["bare"]

KIT = Path(__file__).resolve().parents[1] / ".basicly/core/kit/tracker"


def _load() -> Any:
    if "basicly_tracker_kit_ledger_freeze" in sys.modules:
        return sys.modules["basicly_tracker_kit_ledger_freeze"]
    spec = importlib.util.spec_from_file_location(
        "basicly_tracker_kit_ledger_freeze", KIT / "ledger_freeze.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


freeze = _load()
events = vault_migrate.events


def _ledger(tmp_path: Path) -> Path:
    ledger = tmp_path / ".basicly/ledger"
    events.append(
        ledger,
        [
            events.Draft(
                "demo-a1b2",
                events.KIND_CREATED,
                {
                    "title": "Frozen history",
                    "issue_type": "task",
                    "size": "small",
                    "description": (
                        "## Context\nWhen history moves, "
                        "I want it preserved, so I can keep working."
                    ),
                    "acceptance_criteria": "When a note is added, it shall be readable.",
                    "shaped_under": "dor.v3",
                },
            ),
            events.Draft("demo-a1b2", events.KIND_STATUS, {"status": "open"}),
        ],
    )
    return ledger


def test_freezing_preserves_history_and_installs_a_portable_file(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    original = {path.name: path.read_bytes() for path in ledger.iterdir() if path.is_file()}
    expected = freeze.fingerprint(ledger)

    report = freeze.freeze(ledger, "local-vault.git", "demo", expected)

    assert report["frozen"] is True
    assert ledger.is_file()
    archive = freeze.check(ledger)
    assert archive == ledger.with_name("ledger-archive")
    assert {
        path.name: path.read_bytes() for path in archive.iterdir() if path.is_file()
    } == original
    assert freeze.freeze(ledger, "local-vault.git", "demo", expected) == report


def test_source_changes_are_refused_without_moving_history(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    expected = freeze.fingerprint(ledger)
    events.append(ledger, [events.Draft("demo-a1b2", events.KIND_NOTE, {"text": "New history"})])
    actual = freeze.fingerprint(ledger)

    with pytest.raises(freeze.FreezeError, match="changed after verification"):
        freeze.freeze(ledger, "local-vault.git", "demo", expected)

    assert freeze.fingerprint(ledger) == actual
    assert not ledger.with_name("ledger-archive").exists()
    assert not (tmp_path / ".basicly-tracker.toml").exists()


def test_the_marker_binds_the_normalized_windows_vault_address(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)

    freeze.freeze(ledger, r"C:\tracker\vault.git", "demo", freeze.fingerprint(ledger))

    assert freeze.check(ledger) == ledger.with_name("ledger-archive")


def test_freeze_refuses_an_active_legacy_writer_with_an_open_shard(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    expected = freeze.fingerprint(ledger)
    shard = next(ledger.glob("*.jsonl"))

    with (
        events.LedgerLock(ledger),
        shard.open("ab"),
        pytest.raises(freeze.FreezeError, match="another writer"),
    ):
        freeze.freeze(ledger, "local-vault.git", "demo", expected)

    assert ledger.is_dir()
    assert freeze.fingerprint(ledger) == expected
    assert not ledger.with_name("ledger-archive").exists()
    assert not (tmp_path / ".basicly-tracker.toml").exists()


def test_a_waiting_legacy_writer_cannot_change_a_successfully_frozen_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = _ledger(tmp_path)
    expected = freeze.fingerprint(ledger)
    blocked = threading.Event()
    errors: list[str] = []
    original_lock = events.locking.try_exclusive
    original_rename = Path.rename

    def observe_lock(handle: int) -> bool:
        acquired = original_lock(handle)
        if threading.current_thread().name == "legacy-waiter" and not acquired:
            blocked.set()
        return acquired

    def write() -> None:
        try:
            events.append(
                ledger, [events.Draft("demo-a1b2", events.KIND_NOTE, {"text": "Waiting"})]
            )
        except (OSError, events.LedgerError, freeze.FreezeError) as exc:
            errors.append(str(exc))

    waiter = threading.Thread(target=write, name="legacy-waiter")

    def rename(path: Path, target: Any) -> Path:
        if path == ledger:
            waiter.start()
            assert blocked.wait(10), "the old writer never contended on the held source lock"
        return original_rename(path, target)

    monkeypatch.setattr(events.locking, "try_exclusive", observe_lock)
    monkeypatch.setattr(Path, "rename", rename)
    completed = False
    try:
        completed = bool(freeze.freeze(ledger, "local-vault.git", "demo", expected)["frozen"])
    except freeze.FreezeError:
        assert ledger.is_dir()
    finally:
        if waiter.ident is not None:
            waiter.join(10)
    assert not waiter.is_alive()
    if completed:
        assert errors
        assert freeze.fingerprint(freeze.check(ledger)) == expected
    else:
        assert not (tmp_path / ".basicly-tracker.toml").exists()


def test_an_existing_archive_is_never_overwritten(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    expected = freeze.fingerprint(ledger)
    archive = ledger.with_name("ledger-archive")
    archive.mkdir()
    kept = archive / "kept.txt"
    kept.write_text("Keep this archive", encoding="utf-8")

    with pytest.raises(freeze.FreezeError, match="already exists"):
        freeze.freeze(ledger, "local-vault.git", "demo", expected)

    assert kept.read_text() == "Keep this archive"
    assert freeze.fingerprint(ledger) == expected


def test_an_out_of_protocol_archive_append_is_detected_and_preserved(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    freeze.freeze(ledger, "local-vault.git", "demo", freeze.fingerprint(ledger))
    archive = freeze.check(ledger)
    path = next(archive.glob("*.jsonl"))
    before = path.read_bytes()
    path.write_bytes(before + b"a legacy caller wrote here\n")

    with pytest.raises(freeze.FreezeError, match="changed outside the vault"):
        freeze.check(ledger)

    assert path.read_bytes() == before + b"a legacy caller wrote here\n"


def test_changing_the_vault_config_does_not_rebind_a_frozen_archive(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    freeze.freeze(ledger, "local-vault.git", "demo", freeze.fingerprint(ledger))
    config = tmp_path / ".basicly-tracker.toml"
    config.write_text('vault = "another-vault.git"\nprefix = "demo"\n', encoding="utf-8")

    with pytest.raises(freeze.FreezeError, match="configuration differ"):
        freeze.check(ledger)


def test_a_marker_cannot_redirect_validation_outside_the_archive(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    freeze.freeze(ledger, "local-vault.git", "demo", freeze.fingerprint(ledger))
    marker = json.loads(ledger.read_text())
    marker["files"] = {"../outside.jsonl": "0" * 64}
    ledger.write_text(json.dumps(marker), encoding="utf-8")

    with pytest.raises(freeze.FreezeError, match="invalid archive fingerprint"):
        freeze.check(ledger)


@pytest.mark.parametrize("destination", ["ledger", "ledger-archive"])
def test_direct_appends_to_frozen_history_refuse_by_name(tmp_path: Path, destination: str) -> None:
    ledger = _ledger(tmp_path)
    freeze.freeze(ledger, "local-vault.git", "demo", freeze.fingerprint(ledger))
    archive = freeze.check(ledger)
    before = freeze.fingerprint(archive)

    with pytest.raises(events.LedgerError, match="vault") as refused:
        events.append(
            ledger.with_name(destination),
            [events.Draft("demo-a1b2", events.KIND_NOTE, {"text": "Refused"})],
        )

    assert refused.value.exit_code == 2
    assert freeze.fingerprint(archive) == before


def test_an_interrupted_file_creation_preserves_archive_and_can_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = _ledger(tmp_path)
    expected = freeze.fingerprint(ledger)
    original = Path.open

    def fail_marker(path: Path, *args: Any, **kwargs: Any) -> Any:
        if path == ledger and args and args[0] == "x":
            raise OSError("simulated interrupted marker creation")
        return original(path, *args, **kwargs)

    with monkeypatch.context() as patched:
        patched.setattr(Path, "open", fail_marker)
        with pytest.raises(freeze.FreezeError, match="interrupted"):
            freeze.freeze(ledger, "local-vault.git", "demo", expected)

    assert not ledger.exists()
    assert freeze.fingerprint(ledger.with_name("ledger-archive")) == expected
    freeze.freeze(ledger, "local-vault.git", "demo", expected)
    assert freeze.check(ledger) == ledger.with_name("ledger-archive")


def _migration(tmp_path: Path, bare: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    project = tmp_path / "project"
    ledger = _ledger(project)
    state = tmp_path / "state"
    monkeypatch.setenv("BASICLY_TRACKER_STATE", str(state))
    monkeypatch.setenv("BASICLY_TRACKER_SESSION", "freeze-control")
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("BASICLY_TRACKER_EPHEMERAL", raising=False)
    monkeypatch.chdir(project)
    clone = vault_migrate.vault_clone.locate(state, str(bare))
    vault_migrate.ledger_to_vault(ledger, vault_migrate.Import(clone, lambda: 1700000000.0, True))
    return ledger, state


def test_the_real_freeze_command_verifies_import_then_preserves_the_full_lifecycle(
    tmp_path: Path, bare: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger, state = _migration(tmp_path, bare, monkeypatch)
    before = freeze.fingerprint(ledger)
    code = cli.main([
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
    ])
    report = json.loads(capsys.readouterr().out)
    assert code == 0, report
    assert report["frozen"] is True
    assert freeze.fingerprint(freeze.check(ledger)) == before

    assert cli.main(["comment", str(ledger), "demo-a1b2", "A canonical note after freezing"]) == 0
    note = json.loads(capsys.readouterr().out)
    assert note["events"]
    assert freeze.fingerprint(freeze.check(ledger)) == before
    assert cli.main(["show", str(ledger), "demo-a1b2"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["record"] == "demo-a1b2"
    assert "A canonical note after freezing" in shown["comments"]


def test_freeze_refuses_source_history_missing_from_the_vault_without_local_mutation(
    tmp_path: Path, bare: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger, state = _migration(tmp_path, bare, monkeypatch)
    events.append(ledger, [events.Draft("demo-a1b2", events.KIND_NOTE, {"text": "Not imported"})])
    before = freeze.fingerprint(ledger)
    code = cli.main([
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
    ])
    report = json.loads(capsys.readouterr().out)
    assert code == 1
    assert "do not match the vault import" in report["refused"]
    assert freeze.fingerprint(ledger) == before
    assert not ledger.with_name("ledger-archive").exists()
    assert not (tmp_path / "project/.basicly-tracker.toml").exists()


def test_freeze_refuses_a_template_that_has_not_been_imported(
    tmp_path: Path, bare: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger, state = _migration(tmp_path, bare, monkeypatch)
    (ledger / "template.json").write_text(json.dumps({"stale_days": 21}), encoding="utf-8")
    before = freeze.fingerprint(ledger)

    code = cli.main([
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
    ])

    report = json.loads(capsys.readouterr().out)
    assert code == 1, report
    assert "template" in report["refused"]
    assert freeze.fingerprint(ledger) == before
    assert not ledger.with_name("ledger-archive").exists()
