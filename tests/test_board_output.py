from __future__ import annotations

import argparse
import json
import os
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath
from typing import Any

import pytest

from basicly import (
    board_assets,
    board_bodies,
    board_cli,
    board_facts,
    board_output,
    board_record,
    board_render,
    projection,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "board" / "wall-v1.json"


def _document() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _destinations(document: dict[str, Any]) -> tuple[str, ...]:
    records = board_record.ids(document)
    assert records and board_assets.ASSETS
    return (
        "board.html",
        board_cli.SNAPSHOT_NAME,
        board_cli.KANBAN_NAME,
        board_cli.BACKLOG_NAME,
        *(board_record.href(ident) for ident in records),
        *(board_assets.href(name) for name in board_assets.ASSETS),
    )


@pytest.fixture
def snapshot(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    document = _document()
    monkeypatch.chdir(REPO_ROOT)
    monkeypatch.setattr(board_facts, "document", lambda *_args: document)
    monkeypatch.setattr(board_bodies, "bodies", lambda *_args: {})
    return document


def _emit(out: Path) -> int:
    return board_cli.cmd_emit(argparse.Namespace(out=out, height=None, width=None))


@pytest.mark.parametrize("destination", _destinations(_document()))
@pytest.mark.parametrize("dangling", [False, True])
def test_every_export_refuses_a_linked_destination_and_writes_its_siblings(
    tmp_path: Path,
    snapshot: dict[str, Any],
    capsys: pytest.CaptureFixture[str],
    destination: str,
    dangling: bool,
) -> None:
    out = tmp_path / "output" / "board.html"
    landing = out.parent / destination
    landing.parent.mkdir(parents=True)
    victim = tmp_path / "victim.bin"
    if not dangling:
        victim.write_bytes(b"preserved victim\x00\xff\n")
    landing.symlink_to(victim)

    code = _emit(out)

    if dangling:
        assert not victim.exists()
    else:
        assert victim.read_bytes() == b"preserved victim\x00\xff\n"
    assert landing.is_symlink()
    assert code == 1
    printed = capsys.readouterr()
    assert landing.name in printed.err
    assert "1 output(s) refused" in printed.err + printed.out
    for name in _destinations(snapshot):
        sibling = out.parent / name
        if sibling != landing:
            assert sibling.is_file() and not sibling.is_symlink(), name
            assert sibling.stat().st_size > 0, name


@pytest.mark.parametrize("parent", [board_record.HREF_DIR, board_assets.DIRNAME])
@pytest.mark.parametrize("dangling", [False, True])
def test_linked_child_directories_leave_external_targets_untouched(
    tmp_path: Path,
    snapshot: dict[str, Any],
    capsys: pytest.CaptureFixture[str],
    parent: str,
    dangling: bool,
) -> None:
    out = tmp_path / "output" / "board.html"
    out.parent.mkdir()
    external = tmp_path / "external"
    if not dangling:
        external.mkdir()
    (out.parent / parent).symlink_to(external, target_is_directory=True)

    code = _emit(out)

    if dangling:
        assert not external.exists()
    else:
        assert list(external.iterdir()) == []
    assert code == 1
    expected = sum(name.startswith(parent + "/") for name in _destinations(snapshot))
    printed = capsys.readouterr()
    assert f"{expected} output(s) refused" in printed.err + printed.out
    for name in _destinations(snapshot):
        if not name.startswith(parent + "/"):
            assert (out.parent / name).is_file(), name


@pytest.mark.parametrize("relative", [False, True])
def test_a_linked_output_root_is_refused_before_creating_nested_directories(
    tmp_path: Path,
    snapshot: dict[str, Any],
    capsys: pytest.CaptureFixture[str],
    relative: bool,
) -> None:
    external = tmp_path / "external"
    external.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(external, target_is_directory=True)
    out = alias / "nested" / "board.html"
    supplied = Path(os.path.relpath(out, REPO_ROOT)) if relative else out

    code = _emit(supplied)

    assert list(external.iterdir()) == []
    assert code == 1
    printed = capsys.readouterr()
    assert f"{len(_destinations(snapshot))} output(s) refused" in printed.err + printed.out


@pytest.mark.parametrize("relative", [False, True])
def test_normal_exports_keep_the_declared_pages_and_assets(
    tmp_path: Path,
    snapshot: dict[str, Any],
    capsys: pytest.CaptureFixture[str],
    relative: bool,
) -> None:
    out = tmp_path / "output" / "board.html"
    supplied = Path(os.path.relpath(out, REPO_ROOT)) if relative else out

    assert _emit(supplied) == 0

    actual = {
        path.relative_to(out.parent).as_posix() for path in out.parent.rglob("*") if path.is_file()
    }
    assert actual == set(_destinations(snapshot))
    assert "refused" not in capsys.readouterr().err


@pytest.mark.parametrize(
    "cwd",
    [PurePosixPath("/repo"), PureWindowsPath("C:/repo")],
)
def test_destination_ancestry_keeps_lexical_parent_components(cwd: PurePath) -> None:
    supplied = type(cwd)("output/link/../board.html")

    chain = board_output.destination_chain(supplied, cwd)

    assert chain[0] == type(cwd)(cwd.anchor)
    assert chain[-1] == cwd / supplied
    assert cwd / "output/link" in chain


@pytest.mark.parametrize(
    "path",
    [PurePosixPath("/output/board.html"), PureWindowsPath("D:/output/board.html")],
)
def test_absolute_destination_ancestry_ignores_the_working_directory(path: PurePath) -> None:
    cwd = type(path)(path.anchor) / "unrelated"

    chain = board_output.destination_chain(path, cwd)

    assert chain[-1] == path
    assert cwd not in chain


def test_drive_relative_windows_outputs_are_refused_by_name() -> None:
    with pytest.raises(board_output.OutputRefusedError, match="use an absolute path"):
        board_output.destination_chain(PureWindowsPath("D:board.html"), PureWindowsPath("C:/repo"))
    ordinary = PurePosixPath("D:board.html")
    assert board_output.destination_chain(ordinary, PurePosixPath("/repo"))[-1] == (
        PurePosixPath("/repo") / ordinary
    )


def test_a_link_before_dot_dot_cannot_be_hidden_by_normalization(tmp_path: Path) -> None:
    external = tmp_path / "external"
    external.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(external, target_is_directory=True)
    victim = tmp_path / "victim.bin"
    victim.write_bytes(b"preserved victim\n")

    with pytest.raises(board_output.OutputRefusedError, match="linked"):
        board_output.write_bytes(alias / ".." / victim.name, b"replacement\n")

    assert victim.read_bytes() == b"preserved victim\n"


def test_junction_metadata_refuses_a_parent_before_directory_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = tmp_path / "junction"
    parent.mkdir()
    real = Path.is_junction
    monkeypatch.setattr(Path, "is_junction", lambda path: path == parent or real(path))

    with pytest.raises(board_output.OutputRefusedError, match="linked"):
        board_output.write_bytes(parent / "nested" / "board.html", b"replacement\n")

    assert list(parent.iterdir()) == []


def test_a_leaf_link_inserted_after_the_guard_cannot_change_its_victim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    victim = tmp_path / "victim.bin"
    victim.write_bytes(b"preserved victim\n")
    target = tmp_path / "board.html"
    original = projection.atomic_write_bytes

    def insert_link(path: Path, content: bytes) -> None:
        assert path == target and not path.exists()
        path.symlink_to(victim)
        original(path, content)

    monkeypatch.setattr(projection, "atomic_write_bytes", insert_link)

    board_output.write_bytes(target, b"replacement\n")

    assert victim.read_bytes() == b"preserved victim\n"
    assert not target.is_symlink()
    assert target.read_bytes() == b"replacement\n"


def test_a_page_at_the_root_and_a_record_page_both_resolve_to_the_written_file(
    tmp_path: Path,
) -> None:
    written = board_output.write_assets_beside(board_render.root(), tmp_path)

    assert [path.name for path in written] == list(board_assets.ASSETS)
    root_href = board_assets.href(board_assets.STYLESHEET)
    nested_href = board_assets.href(board_assets.STYLESHEET, nested=True)
    assert (tmp_path / root_href).is_file()
    assert (tmp_path / "record" / nested_href).resolve() == (tmp_path / root_href).resolve()
    assert (tmp_path / root_href).read_bytes() == (
        board_assets.source(board_render.root(), board_assets.STYLESHEET)
    ).read_bytes()


def test_a_linked_asset_is_not_silently_omitted_without_a_reporter(tmp_path: Path) -> None:
    target = tmp_path / board_assets.DIRNAME / board_assets.STYLESHEET
    target.parent.mkdir()
    victim = tmp_path / "victim.bin"
    victim.write_bytes(b"preserved victim\n")
    target.symlink_to(victim)

    with pytest.raises(OSError, match="linked"):
        board_output.write_assets_beside(board_render.root(), tmp_path)

    assert victim.read_bytes() == b"preserved victim\n"
    assert target.is_symlink()


def test_an_asset_reporter_records_refusal_and_other_assets_continue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    templates = tmp_path / "templates"
    vendor = templates / board_assets.DIRNAME
    vendor.mkdir(parents=True)
    (vendor / "first.css").write_bytes(b"first stylesheet\n")
    (vendor / "second.css").write_bytes(b"second stylesheet\n")
    monkeypatch.setattr(board_assets, "ASSETS", {"first.css": "text/css", "second.css": "text/css"})
    out = tmp_path / "output"
    target = out / board_assets.DIRNAME / "first.css"
    target.parent.mkdir(parents=True)
    victim = tmp_path / "victim.bin"
    victim.write_bytes(b"preserved victim\n")
    target.symlink_to(victim)
    refused: list[tuple[Path, OSError]] = []

    written = board_output.write_assets_beside(
        templates, out, on_refusal=lambda path, error: refused.append((path, error))
    )

    assert written == [target.with_name("second.css")]
    assert len(refused) == 1
    assert refused[0][0] == target and "linked" in str(refused[0][1])
    assert victim.read_bytes() == b"preserved victim\n"
    assert written[0].read_bytes() == b"second stylesheet\n"
