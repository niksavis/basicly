from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from basicly import tracker
from tests.flipped_tracker import flipped_repo, seed_records
from tests.kit_deployment_helpers import KIT_RELATIVE, REPO_ROOT, _load

if TYPE_CHECKING:
    from pathlib import Path

cli = _load(REPO_ROOT / KIT_RELATIVE / "cli.py", "tracker_label_shape_cli")


def _run(capsys: pytest.CaptureFixture[str], *args: str) -> dict:
    cli.main(list(args))
    return json.loads(capsys.readouterr().out)


def _labels(ledger: Path, record: str, capsys: pytest.CaptureFixture[str]) -> object:
    return _run(capsys, "show", str(ledger), record)["fields"].get("labels")


def test_label_flags_keep_a_list(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ledger = tmp_path / "ledger"
    record = _run(
        capsys,
        "create",
        str(ledger),
        "--prefix",
        "demo",
        "--title",
        "t",
        "--field",
        'labels=["a","b"]',
    )["record"]
    _run(capsys, "update", str(ledger), record, "--add-label", "c")
    assert _labels(ledger, record, capsys) == ["a", "b", "c"]
    _run(capsys, "update", str(ledger), record, "--remove-label", "a")
    assert _labels(ledger, record, capsys) == ["b", "c"]


@pytest.mark.parametrize("surface", ["kit", "engine"])
def test_a_comma_string_is_stored_as_a_list(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], surface: str
) -> None:
    if surface == "kit":
        ledger = tmp_path / "ledger"
        record = _run(capsys, "create", str(ledger), "--prefix", "demo", "--title", "t")["record"]
        _run(capsys, "update", str(ledger), record, "--field", "labels=a,b")
        assert _labels(ledger, record, capsys) == ["a", "b"]
        return
    repo = flipped_repo(tmp_path)
    seed_records(repo, [{"id": "basicly-aaa", "status": "open", "title": "open work"}])
    tracker.write(repo, ["update", "basicly-aaa", "--labels", "a,b"])
    assert (tracker.read_record(repo, "basicly-aaa") or {}).get("labels") == ["a", "b"]
