from __future__ import annotations

import json
import stat
from pathlib import Path
from typing import IO, Any

import pytest

from basicly import board_snapshot, claude_settings, projection


def test_a_planted_predictable_temporary_link_cannot_change_its_target(tmp_path: Path) -> None:
    target = tmp_path / "settings.json"
    target.write_bytes(b"previous destination\n")
    victim = tmp_path / "victim.bin"
    victim.write_bytes(b"preserved victim\x00\xff\n")
    planted = target.with_name(target.name + ".basicly-tmp")
    planted.symlink_to(victim)

    projection.atomic_write_bytes(target, b"replacement\x00\xff\r\n")

    assert target.read_bytes() == b"replacement\x00\xff\r\n"
    assert not target.is_symlink()
    assert victim.read_bytes() == b"preserved victim\x00\xff\n"
    assert planted.is_symlink()
    assert list(tmp_path.glob("*.basicly-tmp")) == [planted]


@pytest.mark.parametrize("kind", ["file", "symlink"])
def test_a_temporary_collision_refuses_without_deleting_another_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    target = tmp_path / "settings.json"
    target.write_bytes(b"previous destination\n")
    victim = tmp_path / "victim.bin"
    victim.write_bytes(b"preserved victim\n")
    original_open = Path.open
    collisions: list[Path] = []

    def collide(path: Path, mode: str = "r", *args: Any, **kwargs: Any) -> IO[Any]:
        if path != target and path.parent == target.parent and mode in {"wb", "xb"}:
            collisions.append(path)
            if kind == "symlink":
                path.symlink_to(victim)
            else:
                with original_open(path, "xb") as stream:
                    stream.write(b"another writer owns this\n")
        return original_open(path, mode, *args, **kwargs)

    with monkeypatch.context() as scoped:
        scoped.setattr(Path, "open", collide)
        with pytest.raises(FileExistsError):
            projection.atomic_write_bytes(target, b"replacement\n")

    assert len(collisions) == 1
    collision = collisions[0]
    assert target.read_bytes() == b"previous destination\n"
    assert victim.read_bytes() == b"preserved victim\n"
    if kind == "symlink":
        assert collision.is_symlink()
    else:
        assert collision.read_bytes() == b"another writer owns this\n"


def test_replacement_failure_preserves_destination_and_cleans_only_its_temporary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "settings.json"
    target.write_bytes(b"previous destination\n")
    unrelated = tmp_path / "other.basicly-tmp"
    unrelated.write_bytes(b"another writer owns this\n")
    attempted: list[Path] = []

    def refuse(path: Path, destination: Path) -> Path:
        assert destination == target
        assert path.read_bytes() == b"replacement\n"
        assert target.read_bytes() == b"previous destination\n"
        attempted.append(path)
        raise PermissionError("replacement refused")

    monkeypatch.setattr(Path, "replace", refuse)
    with pytest.raises(PermissionError, match="replacement refused"):
        projection.atomic_write_bytes(target, b"replacement\n")

    assert len(attempted) == 1
    assert not attempted[0].exists()
    assert target.read_bytes() == b"previous destination\n"
    assert unrelated.read_bytes() == b"another writer owns this\n"
    assert list(tmp_path.glob("*.basicly-tmp")) == [unrelated]


def test_a_partial_write_failure_closes_and_removes_its_temporary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "settings.json"
    target.write_bytes(b"previous destination\n")
    original_open = Path.open
    opened: list[IO[bytes]] = []

    class FailedWrite:
        def __init__(self, stream: IO[bytes]) -> None:
            self.stream = stream

        def __enter__(self) -> FailedWrite:
            return self

        def write(self, content: bytes) -> int:
            self.stream.write(content[:1])
            raise OSError("partial write refused")

        def __exit__(self, *_args: object) -> None:
            self.stream.close()

    def fail_write(path: Path, mode: str = "r", *args: Any, **kwargs: Any) -> FailedWrite | IO[Any]:
        stream = original_open(path, mode, *args, **kwargs)
        if path != target and path.parent == target.parent and mode in {"wb", "xb"}:
            opened.append(stream)
            return FailedWrite(stream)
        return stream

    with monkeypatch.context() as scoped:
        scoped.setattr(Path, "open", fail_write)
        with pytest.raises(OSError, match="partial write refused"):
            projection.atomic_write_bytes(target, b"replacement\n")

    assert len(opened) == 1
    assert opened[0].closed
    assert target.read_bytes() == b"previous destination\n"
    assert list(tmp_path.glob("*.basicly-tmp")) == []


def test_each_write_uses_a_new_temporary_in_the_destination_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "settings.json"
    original_open = Path.open
    observed: list[Path] = []

    def observe(path: Path, mode: str = "r", *args: Any, **kwargs: Any) -> IO[Any]:
        if path != target and path.parent == target.parent and mode in {"wb", "xb"}:
            observed.append(path)
        return original_open(path, mode, *args, **kwargs)

    with monkeypatch.context() as scoped:
        scoped.setattr(Path, "open", observe)
        projection.atomic_write_text(target, "first\n")
        projection.atomic_write_text(target, "second\n")

    assert len(observed) == 2
    assert observed[0] != observed[1]
    assert target.read_bytes() == b"second\n"
    assert list(tmp_path.glob("*.basicly-tmp")) == []


def test_publication_uses_ordinary_file_creation_permissions(tmp_path: Path) -> None:
    control = tmp_path / "ordinary.bin"
    control.write_bytes(b"control\n")
    target = tmp_path / "settings.json"

    projection.atomic_write_bytes(target, b"replacement\n")

    assert stat.S_IMODE(target.stat().st_mode) == stat.S_IMODE(control.stat().st_mode)


def test_actual_settings_and_snapshot_callers_preserve_their_bytes(tmp_path: Path) -> None:
    settings = tmp_path / claude_settings.CLAUDE_SETTINGS_PATH
    settings.parent.mkdir(parents=True)
    settings.write_text('{"keep": "value"}\n', encoding="utf-8")

    assert claude_settings.set_bg_isolation_none(tmp_path)
    assert json.loads(settings.read_text(encoding="utf-8")) == {
        "keep": "value",
        "worktree": {"bgIsolation": "none"},
    }
    assert not claude_settings.set_bg_isolation_none(tmp_path)
    document = {"schema": "harness-board/v1", "generated_at": "2026-10-24T00:00:00Z"}
    path = board_snapshot.write_document(tmp_path, document)
    assert path.read_text(encoding="utf-8") == board_snapshot.serialize(document)
    assert list(tmp_path.rglob("*.basicly-tmp")) == []
