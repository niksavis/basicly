"""Tests for install provenance state (.basicly/state/install.json)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from basicly.schema import ValidationError
from basicly.state import (
    core_drift,
    read_install_state,
    snapshot_core,
    write_install_state,
)


def _make_core(root: Path) -> Path:
    core = root / "core"
    (core / "fragments").mkdir(parents=True)
    (core / "fragments" / "a.fragment.yaml").write_text("id: a\n", encoding="utf-8")
    (core / "hooks").mkdir()
    (core / "hooks" / "pre-commit.py").write_text("print('x')\n", encoding="utf-8")
    return core


def test_write_and_read_round_trip(tmp_path: Path) -> None:
    """Write then read returns the same version, timestamp, and hash map."""
    core = _make_core(tmp_path)
    state_path = tmp_path / "state" / "install.json"

    written = write_install_state(state_path, "1.2.3", snapshot_core(core))
    loaded = read_install_state(state_path)

    assert loaded is not None
    assert loaded.basicly_version == "1.2.3"
    assert loaded.installed_at == written.installed_at
    assert loaded.core_hashes == written.core_hashes
    assert set(loaded.core_hashes) == {"fragments/a.fragment.yaml", "hooks/pre-commit.py"}


def test_read_missing_state_returns_none(tmp_path: Path) -> None:
    """An absent state file reads as None (pre-provenance install)."""
    assert read_install_state(tmp_path / "install.json") is None


def test_read_corrupt_state_raises(tmp_path: Path) -> None:
    """Unparseable JSON raises instead of being swallowed."""
    state_path = tmp_path / "install.json"
    state_path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValidationError):
        read_install_state(state_path)


def test_read_state_with_missing_keys_raises(tmp_path: Path) -> None:
    """A state file missing required keys raises."""
    state_path = tmp_path / "install.json"
    state_path.write_text(json.dumps({"core": {}}), encoding="utf-8")
    with pytest.raises(ValidationError):
        read_install_state(state_path)


def test_core_drift_reports_modified_and_removed(tmp_path: Path) -> None:
    """Drift lists hand-edited and deleted core files with reasons."""
    core = _make_core(tmp_path)
    written = write_install_state(tmp_path / "state" / "install.json", "1.2.3", snapshot_core(core))

    (core / "fragments" / "a.fragment.yaml").write_text("id: a\nedited: yes\n", encoding="utf-8")
    (core / "hooks" / "pre-commit.py").unlink()

    drift = core_drift(written, core)
    assert ("fragments/a.fragment.yaml", "modified") in drift
    assert ("hooks/pre-commit.py", "removed") in drift


def test_core_drift_empty_when_untouched(tmp_path: Path) -> None:
    """An untouched core reports no drift."""
    core = _make_core(tmp_path)
    written = write_install_state(tmp_path / "install.json", "1.2.3", snapshot_core(core))
    assert core_drift(written, core) == []


def test_snapshot_skips_bytecode_caches(tmp_path: Path) -> None:
    """The snapshot ignores __pycache__/pyc like the materializer does."""
    core = _make_core(tmp_path)
    (core / "hooks" / "__pycache__").mkdir()
    (core / "hooks" / "__pycache__" / "x.pyc").write_bytes(b"\x00")
    assert not any("__pycache__" in key for key in snapshot_core(core))


def test_read_install_state_rejects_a_newer_schema(tmp_path: Path) -> None:
    """A state file from a newer basicly fails with an upgrade hint."""
    state_path = tmp_path / "install.json"
    state_path.write_text(
        json.dumps({
            "schema_version": 99,
            "basicly_version": "9.9.9",
            "installed_at": "2099-01-01T00:00:00+00:00",
            "core": {},
        }),
        encoding="utf-8",
    )
    with pytest.raises(ValidationError, match="upgrade basicly"):
        read_install_state(state_path)
