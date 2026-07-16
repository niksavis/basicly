"""Tests for the single br adapter seam (src/basicly/br.py)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from basicly import br


def test_run_br_raises_when_absent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The hard entry point raises with one canonical absence message."""
    monkeypatch.setattr(br, "which", lambda: None)
    with pytest.raises(RuntimeError, match="br is not on PATH"):
        br.run_br(tmp_path, ["ready"])


def test_try_run_br_returns_none_when_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The soft entry point degrades to None for optional tracker features."""
    monkeypatch.setattr(br, "which", lambda: None)
    assert br.try_run_br(tmp_path, ["sync", "--merge"]) is None


def test_version_probe_warns_below_the_floor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An outdated br gets one warning per process, never a failure."""
    monkeypatch.setattr(br, "which", lambda: "/usr/bin/br")
    monkeypatch.setattr(br, "_probed_paths", set())

    def fake_run(cmd, **_kw):
        out = "br 0.0.1" if "--version" in cmd else ""
        return subprocess.CompletedProcess(cmd, 0, out, "")

    monkeypatch.setattr(br.subprocess, "run", fake_run)
    br.run_br(tmp_path, ["ready"])
    br.run_br(tmp_path, ["ready"])
    err = capsys.readouterr().err
    assert err.count("older than the harness floor") == 1
