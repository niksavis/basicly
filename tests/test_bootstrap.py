"""Tests for the curl bootstrap shim (.scripts/bootstrap.sh / bootstrap.ps1)."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
SH_SCRIPT = REPO_ROOT / ".scripts" / "bootstrap.sh"
PS_SCRIPT = REPO_ROOT / ".scripts" / "bootstrap.ps1"

needs_sh = pytest.mark.skipif(shutil.which("sh") is None, reason="POSIX sh not available")


def test_scripts_exist_and_are_portable() -> None:
    """Both shims exist, target the public repo, and carry no machine paths."""
    pairs = ((SH_SCRIPT, "astral.sh/uv/install.sh"), (PS_SCRIPT, "astral.sh/uv/install.ps1"))
    for script, installer in pairs:
        text = script.read_text(encoding="utf-8")
        assert "https://github.com/niksavis/basicly" in text
        assert installer in text
        assert "/home/" not in text and "C:\\Users" not in text

    assert SH_SCRIPT.read_text(encoding="utf-8").startswith("#!/bin/sh\n")


@needs_sh
def test_bootstrap_sh_parses() -> None:
    """The shim is valid POSIX sh (sh -n)."""
    proc = subprocess.run(["sh", "-n", str(SH_SCRIPT)], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr


@needs_sh
def test_bootstrap_sh_refuses_outside_a_git_repo(tmp_path: Path) -> None:
    """Outside a git repository the shim fails fast with a clear message."""
    proc = subprocess.run(
        ["sh", str(SH_SCRIPT)], cwd=tmp_path, capture_output=True, text=True, check=False
    )
    assert proc.returncode == 1
    assert "consumer git repository" in proc.stderr


@needs_sh
def test_bootstrap_sh_pins_ref_and_passes_args_through(tmp_path: Path) -> None:
    """--ref pins the uvx source; every other argument reaches basicly install."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    stub_bin = tmp_path / "stubbin"
    stub_bin.mkdir()
    log = tmp_path / "uv-args.txt"
    stub = stub_bin / "uv"
    stub.write_text(f'#!/bin/sh\nprintf \'%s\\n\' "$@" > "{log}"\n', encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)

    env = {**os.environ, "PATH": f"{stub_bin}{os.pathsep}{os.environ['PATH']}"}
    proc = subprocess.run(
        ["sh", str(SH_SCRIPT), "--ref", "v9.9.9", "--technologies", "python,zsh"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert log.read_text(encoding="utf-8").splitlines() == [
        "tool",
        "run",
        "--from",
        "git+https://github.com/niksavis/basicly@v9.9.9",
        "basicly",
        "install",
        "--technologies",
        "python,zsh",
    ]
