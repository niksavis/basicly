from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

KIT_DIR = Path(__file__).parent.parent / ".basicly" / "core" / "kit" / "tracker"


def _load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


cli = _load(KIT_DIR / "cli.py", "tracker_cli_templates")

TRIGGER = "When a user files a record, I want it kept, so I can read it back."


def _report(capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    return json.loads(capsys.readouterr().out)


def _template(ledger: Path, body: object) -> None:
    ledger.mkdir(parents=True, exist_ok=True)
    (ledger / "template.json").write_text(json.dumps(body), encoding="utf-8")


def _create(ledger: Path, capsys: pytest.CaptureFixture[str], *extra: str) -> dict[str, Any]:
    argv = ["create", str(ledger), "--prefix", "acme", "--title", "t", "--description", TRIGGER]
    cli.main([*argv, *extra])
    return _report(capsys)


def _shaped_args(*extra: str) -> tuple[str, ...]:
    return ("--acceptance", "- it lists", "--requirements", "- stdlib", *extra)


def test_without_a_template_the_default_rule_holds(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / "ledger"

    assert _create(ledger, capsys)["owed"] == ["## Acceptance Criteria", "## Requirements"]
    assert _create(ledger, capsys, *_shaped_args())["owed"] == []


def test_an_extending_template_adds_a_section_every_record_owes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / "ledger"
    _template(ledger, {"mode": "extend", "sections": ["## Risks"]})

    record = _create(ledger, capsys, *_shaped_args())
    assert record["owed"] == ["## Risks"]
    assert cli.main(["dor", str(ledger), record["record"]]) == cli.EXIT_REFUSED
    assert "--field risks=" in _report(capsys)["remedy"]

    by_field = _create(ledger, capsys, *_shaped_args("--field", "risks=none known"))
    assert by_field["owed"] == []


def test_a_section_in_the_description_satisfies_the_template(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / "ledger"
    _template(ledger, {"sections": ["## Risks"]})

    body = f"{TRIGGER}\n\n## Risks\n\nThe import may time out.\n"
    argv = ["create", str(ledger), "--prefix", "acme", "--description", body, *_shaped_args()]
    assert cli.main(argv) == cli.EXIT_OK
    assert _report(capsys)["owed"] == []


def test_a_placeholder_does_not_satisfy_a_template_section(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / "ledger"
    _template(ledger, {"sections": ["## Risks"]})

    assert _create(ledger, capsys, *_shaped_args("--field", "risks=<risk>"))["owed"] == ["## Risks"]


def test_a_type_section_binds_only_records_of_that_type(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / "ledger"
    _template(ledger, {"types": {"bug": ["## Steps to Reproduce"]}})

    bug = _create(ledger, capsys, *_shaped_args("--field", "issue_type=bug"))
    assert bug["owed"] == ["## Steps to Reproduce"]
    task = _create(ledger, capsys, *_shaped_args("--field", "issue_type=task"))
    assert task["owed"] == []


def test_an_overriding_template_replaces_the_default_rule(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / "ledger"
    _template(ledger, {"mode": "override", "sections": ["## Goal"]})

    argv = ["create", str(ledger), "--prefix", "acme", "--field", "goal=ship it"]
    assert cli.main(argv) == cli.EXIT_OK
    record = _report(capsys)
    assert record["owed"] == []
    assert cli.main(["dor", str(ledger), record["record"]]) == cli.EXIT_OK


def test_an_overriding_template_may_keep_a_default_section(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / "ledger"
    _template(ledger, {"mode": "override", "sections": ["## Trigger", "## Acceptance Criteria"]})

    assert _create(ledger, capsys)["owed"] == ["## Acceptance Criteria"]
    assert _create(ledger, capsys, "--acceptance", "- it lists")["owed"] == []


@pytest.mark.parametrize(
    ("body", "named"),
    [
        ("not json", "is not JSON"),
        ([], "must hold one JSON object"),
        ({"sectons": []}, "unknown key(s) ['sectons']"),
        ({"mode": "replace"}, "mode must be"),
        ({"sections": ["Risks"]}, "'## Heading' strings"),
        ({"types": ["bug"]}, "types must map"),
        ({"schema": "basicly.tracker.template.v2"}, "this kit reads"),
    ],
)
def test_a_malformed_template_is_refused_by_name(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], body: object, named: str
) -> None:
    ledger = tmp_path / "ledger"
    record = _create(ledger, capsys, *_shaped_args())["record"]
    if isinstance(body, str):
        (ledger / "template.json").write_text(body, encoding="utf-8")
    else:
        _template(ledger, body)

    assert cli.main(["dor", str(ledger), record]) == cli.EXIT_REFUSED
    assert named in _report(capsys)["refused"]


def test_the_board_names_a_section_the_template_requires(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / "ledger"
    _template(ledger, {"sections": ["## Risks"]})
    _create(ledger, capsys, *_shaped_args())
    out = tmp_path / "board.html"

    assert cli.main(["board", str(ledger), "--out", str(out)]) == cli.EXIT_OK
    assert "Risks" in out.read_text(encoding="utf-8")


def test_fsck_accepts_a_template_beside_the_log(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / "ledger"
    _template(ledger, {"sections": ["## Risks"]})
    _create(ledger, capsys, *_shaped_args())

    assert cli.main(["fsck", str(ledger)]) == cli.EXIT_OK
