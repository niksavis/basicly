from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

from basicly import verify
from basicly.config import VerifyCheck


def _load_check_runner():
    script_path = (
        Path(__file__).resolve().parents[1] / ".basicly" / "core" / "hooks" / "check_runner.py"
    )
    spec = importlib.util.spec_from_file_location("check_runner_hook", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _repo_relative_tool(repo: Path) -> str:
    tools = repo / "tools"
    tools.mkdir(parents=True)
    if os.name == "nt":
        (tools / "ok.bat").write_text("@exit /b 0\r\n", encoding="ascii")
        return "tools/ok.bat"
    script = tools / "ok"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
    script.chmod(0o755)
    return "tools/ok"


@pytest.fixture
def elsewhere(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    other = tmp_path / "elsewhere"
    other.mkdir()
    monkeypatch.chdir(other)
    return other


@pytest.mark.usefixtures("elsewhere")
def test_hook_check_runner_resolves_a_repo_relative_command_from_any_cwd(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    tool = _repo_relative_tool(repo)
    (repo / "basicly.toml").write_text(
        f'[[verify.checks]]\nname = "relative"\ncommand = ["{tool}"]\nmodes = ["fast"]\n',
        encoding="utf-8",
    )

    assert _load_check_runner().run_checks(repo, "fast") == 0


@pytest.mark.usefixtures("elsewhere")
def test_verify_resolves_a_repo_relative_command_from_any_cwd(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    tool = _repo_relative_tool(repo)
    check = VerifyCheck(name="relative", command=(tool,), modes=frozenset({"full"}))

    result = verify.run_check(check, repo, "full", capture=True)

    assert result.status == "pass", result.detail


def test_a_bare_command_name_is_still_looked_up_on_path(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    module = _load_check_runner()

    assert module.resolve_command(["git", "--version"], repo) == ["git", "--version"]
