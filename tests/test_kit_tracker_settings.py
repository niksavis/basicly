from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.kit_deployment_helpers import KIT_RELATIVE, REPO_ROOT, _load

cli = _load(REPO_ROOT / KIT_RELATIVE / "cli.py", "kit_settings_test_cli")
install_hook = _load(REPO_ROOT / KIT_RELATIVE / "install_hook.py", "kit_settings_test_hook")

SETTINGS = ["fold_on_merge", "holder", "mode", "pin", "prefix", "sections", "stale_days", "types"]
SHAPE = ("--acceptance", "- [ ] it exists", "--requirements", "It exists.")


def _run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, dict[str, Any]]:
    code = cli.main(list(argv))
    return code, json.loads(capsys.readouterr().out)


def _rows(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {row["name"]: row for row in report["settings"]}


def _ledger(tmp_path: Path) -> Path:
    ledger = tmp_path / "ledger"
    ledger.mkdir()
    return ledger


def test_config_names_every_setting_with_its_value_source_and_home(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, report = _run(capsys, "config", str(_ledger(tmp_path)))

    assert code == 0
    assert report["schema"] == "basicly.tracker.config.v1"
    assert [row["name"] for row in report["settings"]] == SETTINGS
    rows = _rows(report)
    assert rows["prefix"] == {
        "name": "prefix",
        "value": None,
        "source": "default",
        "home": "template.json in the ledger",
    }
    assert rows["stale_days"]["value"] == 14
    assert rows["mode"]["value"] == "extend"
    assert rows["fold_on_merge"] == {
        "name": "fold_on_merge",
        "value": False,
        "source": "default",
        "home": "the post-merge git hook",
    }


def test_set_prefix_writes_the_ledger_file_and_config_reads_it_back(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = _ledger(tmp_path)

    code, report = _run(capsys, "config", str(ledger), "set", "prefix", "acme")

    assert code == 0
    assert report["set"] == {"name": "prefix", "value": "acme", "source": "ledger file"}
    held = json.loads((ledger / "template.json").read_text(encoding="utf-8"))
    assert held == {"prefix": "acme"}
    row = _rows(_run(capsys, "config", str(ledger))[1])["prefix"]
    assert (row["value"], row["source"]) == ("acme", "ledger file")


def test_create_without_prefix_mints_under_the_configured_prefix(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = _ledger(tmp_path)
    _run(capsys, "config", str(ledger), "set", "prefix", "acme")

    code, report = _run(capsys, "create", str(ledger), "--title", "t", *SHAPE)

    assert code == 0
    assert report["record"].startswith("acme-")


def test_an_explicit_prefix_wins_over_the_configured_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = _ledger(tmp_path)
    _run(capsys, "config", str(ledger), "set", "prefix", "acme")

    code, report = _run(capsys, "create", str(ledger), "--prefix", "zeta", "--title", "t", *SHAPE)

    assert code == 0
    assert report["record"].startswith("zeta-")


def test_create_with_no_prefix_anywhere_refuses_with_both_remedies(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = _ledger(tmp_path)

    code, report = _run(capsys, "create", str(ledger), "--title", "t", *SHAPE)

    assert code == 1
    assert "--prefix" in report["refused"]
    assert "set prefix" in report["refused"]
    assert not any(ledger.glob("*.jsonl"))


def test_set_refuses_an_unknown_name_with_every_known_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = _ledger(tmp_path)

    code, report = _run(capsys, "config", str(ledger), "set", "colour", "red")

    assert code == 1
    assert "'colour'" in report["refused"]
    assert ", ".join(SETTINGS) in report["refused"]
    assert not (ledger / "template.json").exists()


def test_set_holder_refuses_and_names_the_git_config_command(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = _ledger(tmp_path)

    code, report = _run(capsys, "config", str(ledger), "set", "holder", "alex kim")

    assert code == 1
    assert "`git config basicly.holder 'alex kim'`" in report["refused"]
    assert not (ledger / "template.json").exists()


@pytest.mark.parametrize(
    ("name", "command"),
    [
        ("fold_on_merge", "`basicly-tracker update --fold-on-merge`"),
        ("pin", "`basicly-tracker update`"),
    ],
)
def test_set_of_a_setting_the_installer_owns_names_the_installer_command(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], name: str, command: str
) -> None:
    code, report = _run(capsys, "config", str(_ledger(tmp_path)), "set", name, "true")

    assert code == 1
    assert command in report["refused"]


def test_set_refuses_a_prefix_the_id_rule_refuses(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = _ledger(tmp_path)

    code, report = _run(capsys, "config", str(ledger), "set", "prefix", "ac-me")

    assert code == 1
    assert "'ac-me'" in report["refused"]
    assert not (ledger / "template.json").exists()


def test_set_refuses_a_value_the_template_rule_refuses(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = _ledger(tmp_path)

    code, report = _run(capsys, "config", str(ledger), "set", "stale_days", "0")

    assert code == 1
    assert "stale_days must be a whole number of days" in report["refused"]
    assert not (ledger / "template.json").exists()


def test_set_keeps_every_other_key_of_the_ledger_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = _ledger(tmp_path)
    (ledger / "template.json").write_text('{"sections": ["## Risks"]}', encoding="utf-8")

    code, _ = _run(capsys, "config", str(ledger), "set", "stale_days", "30")

    assert code == 0
    held = json.loads((ledger / "template.json").read_text(encoding="utf-8"))
    assert held == {"sections": ["## Risks"], "stale_days": 30}
    rows = _rows(_run(capsys, "config", str(ledger))[1])
    assert (rows["stale_days"]["value"], rows["stale_days"]["source"]) == (30, "ledger file")
    assert (rows["sections"]["value"], rows["sections"]["source"]) == (["## Risks"], "ledger file")
    assert rows["mode"]["source"] == "default"


def test_the_holder_row_names_the_place_its_value_came_from(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".git" / "config").write_text("[basicly]\n\tholder = kim\n", encoding="utf-8")
    bare = tmp_path / "bare"
    bare.mkdir()
    home = {"HOME": str(tmp_path / "home")}
    lee = {**home, "BASICLY_HOLDER": "lee"}

    chosen = cli.settings.report(ledger, start=repo, environ=home)
    overridden = cli.settings.report(ledger, start=repo, environ=lee)
    nobody = cli.settings.report(ledger, start=bare, environ=home)

    assert _rows(chosen)["holder"]["value"] == "kim"
    assert _rows(chosen)["holder"]["source"] == "git config basicly.holder"
    assert _rows(overridden)["holder"]["value"] == "lee"
    assert _rows(overridden)["holder"]["source"] == "env BASICLY_HOLDER"
    assert (_rows(nobody)["holder"]["value"], _rows(nobody)["holder"]["source"]) == (
        None,
        "default",
    )


def test_fold_on_merge_reads_the_post_merge_hook_of_the_ledger_repository(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / ".basicly" / "ledger"
    ledger.mkdir(parents=True)
    hooks = tmp_path / ".git" / "hooks"
    hooks.mkdir(parents=True)
    (hooks / install_hook.HOOK_NAME).write_text(
        f"#!/bin/sh\n{install_hook.BEGIN}\n{install_hook.END}\n", encoding="utf-8"
    )

    row = _rows(_run(capsys, "config", str(ledger))[1])["fold_on_merge"]

    assert (row["value"], row["source"]) == (True, "git hook post-merge")


def test_the_pin_row_reads_the_ledger_pin(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = _ledger(tmp_path)
    cli.pin.write(ledger)

    row = _rows(_run(capsys, "config", str(ledger))[1])["pin"]

    assert (row["value"], row["source"]) == (cli.pin.KIT_VERSION, "ledger file")
