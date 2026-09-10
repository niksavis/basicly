"""`basicly check` against a catalog another version installed (basicly-lc2bd3v).

Reported from a consumer whose CI went red with no commit of theirs: check named the
skew in a `Note:` line, then compared this engine's templates against the other
version's output and told them to run `basicly build`. The files differ by construction,
and build under either version leaves it red — the fix was the line above it.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import TYPE_CHECKING

from basicly import __version__
from basicly.cli import cmd_check

if TYPE_CHECKING:
    import pytest


def _repo(tmp_path: Path, installed_version: str | None) -> Path:
    """A repo carrying only what the skew check reads, plus install state when given."""
    (tmp_path / "basicly.toml").write_text("", encoding="utf-8")
    if installed_version is not None:
        state = tmp_path / ".basicly" / "state" / "install.json"
        state.parent.mkdir(parents=True)
        state.write_text(
            json.dumps({
                "schema_version": 1,
                "basicly_version": installed_version,
                "installed_at": "2026-07-23T00:00:00+00:00",
                "core": {},
            }),
            encoding="utf-8",
        )
    return tmp_path


def test_a_catalog_another_version_installed_is_refused_before_comparing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The remedy must be `install`, and no phantom hash list may follow it."""
    monkeypatch.chdir(_repo(tmp_path, "0.5.1"))

    exit_code = cmd_check(argparse.Namespace())

    err = capsys.readouterr().err
    assert exit_code == 1
    assert "Version skew" in err
    assert "0.5.1" in err and __version__ in err
    assert "basicly install" in err
    assert "Stale generated files detected" not in err, "it compared across versions anyway"


def test_a_matching_version_is_not_refused_for_skew(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The control: an install by this engine must reach the real staleness check."""
    monkeypatch.chdir(_repo(tmp_path, __version__))

    cmd_check(argparse.Namespace())

    assert "Version skew" not in capsys.readouterr().err


def test_no_install_state_is_not_refused_for_skew(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The authoring repo writes no state file, and is not a skewed consumer."""
    monkeypatch.chdir(_repo(tmp_path, None))

    cmd_check(argparse.Namespace())

    assert "Version skew" not in capsys.readouterr().err
