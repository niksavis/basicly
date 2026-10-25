from __future__ import annotations

import importlib.util
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath

import pytest

from basicly import hooks, lefthook_config, verify
from basicly.config import VerifyCheck
from tests.native_hooks import repository

ROOT = Path(__file__).resolve().parents[1]


def test_strict_hook_reports_unicode_without_a_child_output_crash(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("# Ő\nx = 1\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(ROOT / ".basicly/core/hooks/no-comments.py"), "a.py"],
        cwd=tmp_path,
        env={**os.environ, "PYTHONIOENCODING": "ascii"},
        capture_output=True,
        check=False,
    )
    assert result.returncode == 1
    assert "Ő" in result.stdout.decode("utf-8")
    assert b"Traceback" not in result.stderr


@pytest.mark.parametrize("capture", [True, False])
@pytest.mark.parametrize("code", [0, 7])
def test_verify_preserves_unicode_and_failure_status(
    tmp_path: Path, capture: bool, code: int, capsys: pytest.CaptureFixture[str]
) -> None:
    command = (
        sys.executable,
        "-c",
        f"import sys;sys.stdout.buffer.write('Ő\\n'.encode('utf-8'));sys.exit({code})",
    )
    result = verify.run_check(
        VerifyCheck(name="unicode", command=command, modes=frozenset({"full"})),
        tmp_path,
        "full",
        capture=capture,
    )
    assert result.status == ("fail" if code else "pass")
    assert result.returncode == code
    assert "Ő" in result.output
    if not capture:
        assert "Ő" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("root", "program", "expected"),
    [
        (PurePosixPath("/repo"), "tools/check", "/repo/tools/check"),
        (PurePosixPath("/repo"), "bare\\tool", "bare\\tool"),
        (PurePosixPath("/repo"), "git", "git"),
        (PureWindowsPath("C:/repo"), "tools/check.exe", "C:\\repo\\tools\\check.exe"),
        (PureWindowsPath("C:/repo"), "tools\\check.exe", "C:\\repo\\tools\\check.exe"),
        (PureWindowsPath("C:/repo"), "C:/tools/check.exe", "C:/tools/check.exe"),
        (PureWindowsPath("C:/repo"), "git", "git"),
    ],
)
def test_program_resolution_preserves_each_platform_path_form(
    root: PurePosixPath | PureWindowsPath, program: str, expected: str
) -> None:
    source = ROOT / ".basicly/core/hooks/command_paths.py"
    spec = importlib.util.spec_from_file_location("compatibility_command_paths", source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.resolve_command([program, "argument"], root) == [expected, "argument"]


def test_native_jobs_preserve_quoted_literals_and_original_configuration(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    script = repo / ".basicly/core/hooks/arguments.py"
    script.write_text(
        "import json,sys\nfrom pathlib import Path\n"
        "Path('arguments.json').write_text(json.dumps(sys.argv[1:]))\n",
        encoding="utf-8",
    )
    arguments = [
        r"back\"quote",
        r'two\\"quote',
        "end\\",
        '"',
        "two words",
        "apostrophe's",
        'a"quote',
        r"back\slash",
        "$value",
        ">=3.14",
    ]
    command = shlex.join([Path(sys.executable).as_posix(), script.as_posix(), *arguments])
    config = repo / "lefthook.yml"
    config.write_text(
        "post-merge:\n  commands:\n    consumer:\n      run: " + json.dumps(command) + "\n",
        encoding="utf-8",
    )
    (repo / ".basicly/core/hooks/allow.py").write_text("raise SystemExit(0)\n", encoding="utf-8")
    lefthook_config.sync_config(
        repo,
        [hooks.HookSpec("allow", "allow.py", "post-merge", always_run=True)],
        ".basicly/core/hooks",
    )
    assert hooks.install_hooks(repo, ["post-merge"])[0]
    before = config.read_bytes()
    result = subprocess.run(
        ["git", "hook", "run", "post-merge"], cwd=repo, capture_output=True, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads((repo / "arguments.json").read_text()) == arguments
    assert config.read_bytes() == before
