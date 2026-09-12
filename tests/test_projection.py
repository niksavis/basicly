from __future__ import annotations

from pathlib import Path

from basicly import projection
from basicly.projection import SyncResult, sync_file, write_if_changed


def test_writes_when_absent(tmp_path: Path) -> None:
    path = tmp_path / "a.txt"
    assert write_if_changed(path, b"hello") is True
    assert path.read_bytes() == b"hello"


def test_skips_when_identical(tmp_path: Path) -> None:
    path = tmp_path / "a.txt"
    path.write_bytes(b"hello")
    assert write_if_changed(path, b"hello") is False


def test_writes_when_changed(tmp_path: Path) -> None:
    path = tmp_path / "a.txt"
    path.write_bytes(b"hello")
    assert write_if_changed(path, b"world") is True
    assert path.read_bytes() == b"world"


def test_creates_parent_dirs(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "deep" / "a.txt"
    assert write_if_changed(path, b"x") is True
    assert path.read_bytes() == b"x"


def test_crlf_content_is_byte_exact(tmp_path: Path) -> None:

    path = tmp_path / "hook.py"
    path.write_bytes(b"line1\r\nline2\r\n")
    assert write_if_changed(path, b"line1\r\nline2\r\n") is False
    assert write_if_changed(path, b"line1\nline2\n") is True
    assert path.read_bytes() == b"line1\nline2\n"


def test_sync_file_records_written_then_unchanged(tmp_path: Path) -> None:
    result = SyncResult()
    path = tmp_path / "a.txt"
    sync_file(path, b"x", result)
    assert result.written == [path]
    assert result.unchanged == []
    sync_file(path, b"x", result)
    assert result.written == [path]
    assert result.unchanged == [path]


def test_atomic_write_leaves_no_tmp_and_replaces_content(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "settings.json"
    projection.atomic_write_text(target, '{"a": 1}\n')
    assert target.read_text(encoding="utf-8") == '{"a": 1}\n'
    projection.atomic_write_text(target, '{"a": 2}\n')
    assert target.read_text(encoding="utf-8") == '{"a": 2}\n'
    assert list(tmp_path.rglob("*.basicly-tmp")) == []
