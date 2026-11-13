from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.test_kit_vault_canonical import PRIVATE, _source, events
from tests.test_kit_vault_migrate import _head, vault_migrate
from tests.test_kit_vault_share import bare
from tests.test_kit_vault_view import cli

__all__ = ["bare"]


@pytest.fixture(autouse=True)
def _isolated_working_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)


def _run(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, dict]:
    code = cli.main(["migrate", *args])
    return code, json.loads(capsys.readouterr().out)


def test_preview_reports_canonical_counts_without_fetching_or_changing_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "source"
    _source(source)
    before = {path.name: path.read_bytes() for path in source.iterdir() if path.is_file()}
    state = tmp_path / "state"

    def refuse_fetch(*_args: Any, **_kwargs: Any) -> None:
        pytest.fail("preview fetched a vault")

    monkeypatch.setattr(vault_migrate.vault_clone, "fetch", refuse_fetch)
    code, report = _run(
        capsys,
        str(source),
        "--to-vault",
        "--preview",
        "--state",
        str(state),
        "--redact-name",
        PRIVATE,
    )
    assert code == 0
    assert report["preview"] is True
    assert (report["events"], report["canonical_events"], report["records"]) == (4, 4, 1)
    assert report["reminted"] == 1
    assert PRIVATE not in json.dumps(report)
    assert len(report["digest"]) == 64
    assert not state.exists()
    assert {path.name: path.read_bytes() for path in source.iterdir() if path.is_file()} == before


def test_preview_uses_the_same_canonical_digest_as_an_import(
    tmp_path: Path,
    bare: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "source"
    _source(source)
    options = [str(source), "--to-vault", "--redact-name", PRIVATE]
    _, preview = _run(capsys, *options, "--preview")
    code, imported = _run(
        capsys, *options, "--vault", str(bare), "--state", str(tmp_path / "state"), "--no-map-kept"
    )
    assert code == 0, imported
    assert imported["mapping"] == "not-kept"
    assert imported["digest"] == preview["digest"]


def test_preview_defaults_to_the_current_ledger_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "standalone-ledger"
    _source(source)
    monkeypatch.chdir(source)
    code, report = _run(capsys, "--to-vault", "--preview")
    assert code == 0, report
    assert (report["records"], report["events"]) == (1, 4)


def test_import_saves_only_ids_and_field_names_in_an_explicit_private_map(
    tmp_path: Path,
    bare: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "source"
    found = _source(source)
    mapping = tmp_path / "private-map.json"
    state = tmp_path / "state"
    options = [
        str(source),
        "--to-vault",
        "--vault",
        str(bare),
        "--state",
        str(state),
        "--redact-name",
        PRIVATE,
        "--private-map",
        str(mapping),
    ]
    code, report = _run(capsys, *options)
    assert code == 0, report
    rows = json.loads(mapping.read_text())["events"]
    assert len(rows) == len(found)
    assert {row["old"] for row in rows} == {event.id for event in found}
    assert all(set(row) == {"old", "new", "fields"} for row in rows)
    assert PRIVATE not in mapping.read_text()
    clone = vault_migrate.vault_clone.locate(state, str(bare))
    valid = vault_migrate.vault_clone.valid_events(clone)
    landed = vault_migrate.vault_view.carried(valid, vault_migrate.vault_view.Want("demo"))
    assert {row["new"] for row in rows} == {event.id for event in landed}
    assert landed[0].payload["assignee"] == "contributor"
    assert found[0].id not in json.dumps(valid)
    assert found[0].id not in json.dumps(report)
    before = _head(bare)
    content = mapping.read_bytes()
    code, refused = _run(capsys, *options, "--overwrite-private-map")
    assert code == 1
    assert "already carries" in refused["refused"]
    assert _head(bare) == before
    assert mapping.read_bytes() == content
    assert events.read_events(source)[0] == found


def test_import_requires_a_mapping_choice_before_it_fetches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "source"
    _source(source)

    def refuse_fetch(*_args: Any, **_kwargs: Any) -> None:
        pytest.fail("an invalid invocation fetched a vault")

    monkeypatch.setattr(vault_migrate.vault_clone, "fetch", refuse_fetch)
    code, report = _run(capsys, str(source), "--to-vault")
    assert code == 1
    assert "--private-map FILE or --no-map-kept" in report["refused"]


def test_unknown_source_format_refuses_by_name(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as refused:
        cli.main(["migrate", "--to-vault", "--preview", "--from", "unknown-format"])
    assert refused.value.code == 2
    assert "unknown-format" in capsys.readouterr().err


def test_preview_accepts_br_as_its_own_format_key(
    capsys: pytest.CaptureFixture[str],
) -> None:
    fixture = Path(__file__).parent / "fixtures" / "vault-migrate" / "br.jsonl"
    code, report = _run(capsys, "--to-vault", "--preview", "--from", "br", "--source", str(fixture))
    assert code == 0, report
    assert report["records"] == 2
    assert report["canonical_events"] == report["events"]
