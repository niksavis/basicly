from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
SH_SCRIPT = REPO_ROOT / ".scripts" / "bootstrap.sh"
PS_SCRIPT = REPO_ROOT / ".scripts" / "bootstrap.ps1"

needs_sh = pytest.mark.skipif(shutil.which("sh") is None, reason="POSIX sh not available")


def test_scripts_exist_and_are_portable() -> None:
    pairs = ((SH_SCRIPT, "astral.sh/uv/install.sh"), (PS_SCRIPT, "astral.sh/uv/install.ps1"))
    for script, installer in pairs:
        text = script.read_text(encoding="utf-8")
        assert "https://github.com/niksavis/basicly" in text
        assert installer in text
        assert "/home/" not in text and "C:\\Users" not in text

    assert SH_SCRIPT.read_text(encoding="utf-8").startswith("#!/bin/sh\n")


@needs_sh
def test_bootstrap_sh_parses() -> None:
    proc = subprocess.run(["sh", "-n", str(SH_SCRIPT)], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr


@needs_sh
def test_bootstrap_sh_refuses_outside_a_git_repo(tmp_path: Path) -> None:
    proc = subprocess.run(
        ["sh", str(SH_SCRIPT)], cwd=tmp_path, capture_output=True, text=True, check=False
    )
    assert proc.returncode == 1
    assert "consumer git repository" in proc.stderr


@needs_sh
def test_bootstrap_sh_pins_ref_and_passes_args_through(tmp_path: Path) -> None:
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
        "--python",
        ">=3.14",
        "--from",
        "https://github.com/niksavis/basicly/releases/download/v9.9.9/basicly-9.9.9-py3-none-any.whl",
        "basicly",
        "install",
        "--technologies",
        "python,zsh",
    ]


@needs_sh
def test_bootstrap_latest_resolves_to_a_release_wheel(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    binaries = tmp_path / "bin"
    binaries.mkdir()
    log = tmp_path / "arguments"
    uv = binaries / "uv"
    uv.write_text(f'#!/bin/sh\nprintf \'%s\\n\' "$@" > "{log}"\n')
    uv.chmod(0o755)
    result = subprocess.run(
        [
            "sh",
            "-c",
            "script=$1; shift; curl() { printf "
            'https://github.com/niksavis/basicly/releases/tag/v9.9.9; }; . "$script"',
            "sh",
            str(SH_SCRIPT),
        ],
        cwd=tmp_path,
        env={**os.environ, "PATH": str(binaries) + os.pathsep + os.environ["PATH"]},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert log.read_text().splitlines()[5] == (
        "https://github.com/niksavis/basicly/releases/download/v9.9.9/basicly-9.9.9-py3-none-any.whl"
    )


@needs_sh
def test_bootstrap_refuses_source_branches_before_running_basicly(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    result = subprocess.run(
        ["sh", str(SH_SCRIPT), "--ref", "main"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "must name a release" in result.stderr


@needs_sh
def test_bootstrap_propagates_install_failure(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    binary = tmp_path / "bin"
    binary.mkdir()
    stub = binary / "uv"
    stub.write_text("#!/bin/sh\nexit 23\n")
    stub.chmod(0o755)
    result = subprocess.run(
        ["sh", str(SH_SCRIPT), "--ref", "v9.9.9"],
        cwd=tmp_path,
        env={**os.environ, "PATH": str(binary) + os.pathsep + os.environ["PATH"]},
        capture_output=True,
        check=False,
    )
    assert result.returncode == 23


@pytest.mark.skipif(os.name != "nt", reason="native PowerShell command discovery")
@pytest.mark.parametrize("status", [0, 23])
def test_powershell_bootstrap_latest_and_failure(tmp_path: Path, status: int) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    log = tmp_path / "arguments.json"
    writer = tmp_path / "capture.py"
    writer.write_text(
        "import json, sys\nfrom pathlib import Path\n"
        f"Path({str(log)!r}).write_text(json.dumps(sys.argv[1:]))\nraise SystemExit({status})\n"
    )
    wrapper = tmp_path / "invoke.ps1"
    wrapper.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        f"function uv {{ & '{sys.executable}' '{writer}' @args }}\n"
        "function Invoke-WebRequest {\n"
        "[pscustomobject]@{BaseResponse=[pscustomobject]@{ResponseUri="
        "[uri]'https://github.com/niksavis/basicly/releases/tag/v9.9.9'}}\n}\n"
        f"& '{str(PS_SCRIPT).replace(chr(39), chr(39) * 2)}' --technologies python\n"
        "Write-Output 'shell-still-running'\n"
    )
    result = subprocess.run(
        ["powershell", "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(wrapper)],
        cwd=tmp_path,
        env={**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"]},
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert (result.returncode == 0) == (status == 0), result.stdout + result.stderr
    assert json.loads(log.read_text()) == [
        "tool",
        "run",
        "--python",
        ">=3.14",
        "--from",
        "https://github.com/niksavis/basicly/releases/download/v9.9.9/basicly-9.9.9-py3-none-any.whl",
        "basicly",
        "install",
        "--technologies",
        "python",
    ]
    assert ("shell-still-running" in result.stdout) == (status == 0)
