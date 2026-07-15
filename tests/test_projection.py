"""Tests for the shared projection engine (write-if-changed + sync recording)."""

from __future__ import annotations

from pathlib import Path

from basicly.projection import SyncResult, sync_file, write_if_changed


def test_writes_when_absent(tmp_path: Path) -> None:
    """A missing destination is written and reported as changed."""
    path = tmp_path / "a.txt"
    assert write_if_changed(path, b"hello") is True
    assert path.read_bytes() == b"hello"


def test_skips_when_identical(tmp_path: Path) -> None:
    """Byte-identical content is not rewritten."""
    path = tmp_path / "a.txt"
    path.write_bytes(b"hello")
    assert write_if_changed(path, b"hello") is False


def test_writes_when_changed(tmp_path: Path) -> None:
    """Different content is written and reported as changed."""
    path = tmp_path / "a.txt"
    path.write_bytes(b"hello")
    assert write_if_changed(path, b"world") is True
    assert path.read_bytes() == b"world"


def test_creates_parent_dirs(tmp_path: Path) -> None:
    """Missing parent directories are created on write."""
    path = tmp_path / "nested" / "deep" / "a.txt"
    assert write_if_changed(path, b"x") is True
    assert path.read_bytes() == b"x"


def test_crlf_content_is_byte_exact(tmp_path: Path) -> None:
    """Comparison is byte-exact: a CRLF file is untouched when its bytes match.

    Regression for the projection-unification crux: unifying on text comparison
    would newline-normalize and spuriously rewrite (or corrupt) a CRLF hook script.
    """
    path = tmp_path / "hook.py"
    path.write_bytes(b"line1\r\nline2\r\n")
    assert write_if_changed(path, b"line1\r\nline2\r\n") is False
    # An LF-normalized version differs at the byte level, so it is a real change.
    assert write_if_changed(path, b"line1\nline2\n") is True
    assert path.read_bytes() == b"line1\nline2\n"


def test_sync_file_records_written_then_unchanged(tmp_path: Path) -> None:
    """sync_file records a path under written on change and unchanged otherwise."""
    result = SyncResult()
    path = tmp_path / "a.txt"
    sync_file(path, b"x", result)
    assert result.written == [path]
    assert result.unchanged == []
    sync_file(path, b"x", result)
    assert result.written == [path]
    assert result.unchanged == [path]
