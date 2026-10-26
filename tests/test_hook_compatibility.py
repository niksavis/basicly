from __future__ import annotations

import importlib.util
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath

import pytest
import yaml

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


STAGED_FILENAMES = ["two words.py", "apostrophe's.py", "Ő.py"]
RECORDER = (
    "import json,sys\nfrom pathlib import Path\n"
    "Path('arguments.json').write_text(json.dumps(sys.argv[1:]), encoding='utf-8')\n"
)


def _prepared(tmp_path: Path) -> tuple[Path, Path]:
    repo = repository(tmp_path)
    script = repo / ".basicly/core/hooks/arguments.py"
    script.write_text(RECORDER, encoding="utf-8")
    for filename in STAGED_FILENAMES:
        (repo / filename).write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "--", *STAGED_FILENAMES], cwd=repo, check=True)
    return repo, script


def _run_post_merge(repo: Path) -> subprocess.CompletedProcess[bytes]:
    assert hooks.install_hooks(repo, ["post-merge"])[0]
    return subprocess.run(
        ["git", "hook", "run", "post-merge"], cwd=repo, capture_output=True, check=False
    )


def _recorded(repo: Path) -> list[str] | None:
    path = repo / "arguments.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def test_the_managed_job_hands_every_staged_filename_to_the_hook_unchanged(
    tmp_path: Path,
) -> None:
    repo, _ = _prepared(tmp_path)
    lefthook_config.sync_config(
        repo,
        [hooks.HookSpec("arguments", "arguments.py", "post-merge", pass_filenames=True)],
        ".basicly/core/hooks",
    )
    result = _run_post_merge(repo)
    assert result.returncode == 0, (result.stdout + result.stderr).decode(
        "utf-8", "backslashreplace"
    )
    assert _recorded(repo) == sorted(STAGED_FILENAMES)


def test_the_managed_job_puts_no_filename_on_the_command_line() -> None:
    generated = lefthook_config.managed_config(
        [hooks.HookSpec("arguments", "arguments.py", "post-merge", pass_filenames=True)],
        ".basicly/core/hooks",
    )
    jobs = yaml.safe_load(generated)["post-merge"]["commands"]
    assert jobs, "the managed configuration declares no job"
    placeholders = ("{staged_files}", "{push_files}", "{all_files}", "{files}")
    for name, job in jobs.items():
        for placeholder in placeholders:
            assert placeholder not in job["run"], f"{name} expands {placeholder}"
        for filename in STAGED_FILENAMES:
            assert filename not in job["run"], f"{name} names {filename}"


@pytest.mark.parametrize(
    "argument",
    [
        pytest.param("{staged_files}", id="expanded-filenames"),
        pytest.param('a"quote', id="literal-quote"),
    ],
)
def test_a_minimal_consumer_job_keeps_the_upstream_argument_behaviour(
    tmp_path: Path, argument: str
) -> None:
    repo, script = _prepared(tmp_path)
    (repo / ".basicly/core/hooks/allow.py").write_text("raise SystemExit(0)\n", encoding="utf-8")
    lefthook_config.sync_config(
        repo,
        [hooks.HookSpec("allow", "allow.py", "post-merge", always_run=True)],
        ".basicly/core/hooks",
    )
    command = shlex.join([Path(sys.executable).as_posix(), script.as_posix(), argument])
    config = repo / "lefthook.yml"
    before = config.read_bytes()
    config.write_bytes(
        before
        + (
            "post-merge:\n  commands:\n    consumer:\n      run: " + json.dumps(command) + "\n"
        ).encode("utf-8")
    )
    amended = config.read_bytes()
    result = _run_post_merge(repo)
    assert config.read_bytes() == amended

    intended = sorted(STAGED_FILENAMES) if argument == "{staged_files}" else [argument]
    if os.name == "nt":
        assert result.returncode != 0, "upstream Lefthook 2.2.2 now carries the quote on Windows"
        assert _recorded(repo) is None
    else:
        assert result.returncode == 0, (result.stdout + result.stderr).decode(
            "utf-8", "backslashreplace"
        )
        assert _recorded(repo) == intended
