from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

import basicly
from basicly import release
from tests.kit_deployment_helpers import KIT_RELATIVE, REPO_ROOT, _load

pin = _load(REPO_ROOT / KIT_RELATIVE / "pin.py", "kit_pin_test_pin")
cli = _load(REPO_ROOT / KIT_RELATIVE / "cli.py", "kit_pin_test_cli")
install_hook = _load(REPO_ROOT / KIT_RELATIVE / "install_hook.py", "kit_pin_test_install_hook")


def test_the_kit_version_is_the_engine_version() -> None:
    assert basicly.__version__ == pin.KIT_VERSION


def test_a_ledger_with_no_pin_or_the_same_pin_runs(tmp_path: Path) -> None:
    pin.require(tmp_path)
    pin.write(tmp_path)

    pin.require(tmp_path)

    assert pin.pinned(tmp_path) == pin.KIT_VERSION


def test_a_ledger_pinned_to_another_version_refuses_with_the_install_command(
    tmp_path: Path,
) -> None:
    (tmp_path / pin.PIN_FILE).write_text("0.1.0\n", encoding="utf-8")

    with pytest.raises(pin.PinMismatchError, match=r"uv tool install --force .*@v0\.1\.0"):
        pin.require(tmp_path)


def test_every_kit_command_refuses_on_a_mismatched_pin(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    shape = ["--acceptance", "- [ ] it exists", "--requirements", "It exists."]
    assert cli.main(["create", str(tmp_path), "--prefix", "p", "--title", "t", *shape]) == 0
    capsys.readouterr()
    (tmp_path / pin.PIN_FILE).write_text("0.1.0\n", encoding="utf-8")

    code = cli.main(["ready", str(tmp_path)])

    report = json.loads(capsys.readouterr().out)
    assert code != 0
    assert "pins tracker 0.1.0" in report["refused"]


def test_install_pins_the_ledger_only_when_asked(tmp_path: Path) -> None:
    ledger = tmp_path / ".basicly" / "ledger"
    install_hook.ensure_ledger(tmp_path, ledger, dry_run=False, stream=io.StringIO())
    assert pin.pinned(ledger) is None

    install_hook.pin_ledger(ledger, dry_run=False, stream=io.StringIO())

    assert pin.pinned(ledger) == pin.KIT_VERSION


def test_a_ledger_holding_only_its_pin_reads_as_a_fresh_ledger(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pin.write(tmp_path)

    assert cli.main(["ready", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)["count"] == 0


def test_the_standalone_installer_asks_for_the_pin() -> None:
    source = REPO_ROOT / "packages" / "basicly-tracker" / "basicly_tracker" / "__init__.py"
    assert '"--pin"' in source.read_text(encoding="utf-8")


def test_a_release_bump_moves_the_kit_version(tmp_path: Path) -> None:
    version_file = tmp_path / release.VERSION_FILE
    version_file.parent.mkdir(parents=True)
    version_file.write_text('__version__ = "1.2.3"\n', encoding="utf-8")
    kit_file = tmp_path / release.KIT_VERSION_FILE
    kit_file.parent.mkdir(parents=True)
    kit_file.write_text('KIT_VERSION = "1.2.3"\n', encoding="utf-8")
    plan = release.ReleasePlan(current_version="1.2.3", version="1.2.4", date="2026-09-25", pins=())

    release._bump_version_file(tmp_path, plan)

    assert kit_file.read_text(encoding="utf-8") == 'KIT_VERSION = "1.2.4"\n'
