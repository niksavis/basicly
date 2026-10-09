from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from basicly import cli, config, owned_store
from tests.flipped_tracker import flipped_repo, seed_records

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


def _repo(tmp_path: Path) -> Path:
    repo = flipped_repo(tmp_path)
    seed_records(repo, [{"id": "basicly-aaa", "status": "open", "title": "open work"}])
    return repo


def _start(repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> str:
    monkeypatch.chdir(repo)
    assert cli.main(["session", "start"]) == 0
    return capsys.readouterr().out


@pytest.fixture
def restored_kit_version_reader() -> Iterator[None]:
    yield
    owned_store.set_kit_version_reader(config.installed_kit_version)


@pytest.mark.usefixtures("restored_kit_version_reader")
def test_session_start_names_the_kit_refusal_in_place_of_no_handover(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _repo(tmp_path)
    owned_store.set_kit_version_reader(lambda _root: "0.0.1")
    out = _start(repo, monkeypatch, capsys)
    assert "handover: unknown - the ledger was not read" in out
    assert "was installed by basicly 0.0.1" in out
    assert "handover: none" not in out


def test_session_start_says_no_handover_when_the_kit_is_readable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    out = _start(_repo(tmp_path), monkeypatch, capsys)
    assert "handover: none" in out
    assert "handover: unknown" not in out
