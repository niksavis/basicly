from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest


def _load_identity_guard_module():
    script_path = (
        Path(__file__).resolve().parents[2] / ".basicly" / "core" / "hooks" / "identity-guard.py"
    )
    spec = importlib.util.spec_from_file_location("identity_guard_hook", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_accepts_a_real_identity() -> None:
    module = _load_identity_guard_module()
    ok, _ = module.check_identity("Ada Lovelace", "ada@personal.example")
    assert ok


def test_rejects_missing_email() -> None:
    module = _load_identity_guard_module()
    ok, message = module.check_identity("Ada Lovelace", "")
    assert not ok
    assert "user.email" in message


def test_rejects_hostname_fallback_email() -> None:
    module = _load_identity_guard_module()
    for bad in ("user@workstation.local", "user@host.(none)", "user@box.localdomain"):
        ok, message = module.check_identity("Ada Lovelace", bad)
        assert not ok, bad
        assert "auto-generated" in message


def test_rejects_missing_name() -> None:
    module = _load_identity_guard_module()
    ok, message = module.check_identity("", "ada@personal.example")
    assert not ok
    assert "user.name" in message


def test_allow_email_pattern_enforced_when_set() -> None:
    module = _load_identity_guard_module()
    ok_match, _ = module.check_identity("Ada Lovelace", "ada@acme.example", r"@acme\.example$")
    assert ok_match
    ok_miss, message = module.check_identity(
        "Ada Lovelace", "ada@personal.example", r"@acme\.example$"
    )
    assert not ok_miss
    assert "identityAllowEmail" in message


def _init_repo(path: Path, name: str, email: str, allow_email: str = "") -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", name], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", email], cwd=path, check=True)
    if allow_email:
        subprocess.run(
            ["git", "config", "basicly.identityAllowEmail", allow_email], cwd=path, check=True
        )


def _run_main(module, cwd: Path, monkeypatch: pytest.MonkeyPatch, **env: str) -> int:
    monkeypatch.chdir(cwd)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return module.main()


def test_effective_identity_reads_env_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_identity_guard_module()
    _init_repo(tmp_path, "Human Dev", "human@company.com")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GIT_COMMITTER_NAME", "basicly-bot")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "bot@example.com")
    name, email = module.effective_identity("COMMITTER", tmp_path)
    assert (name, email) == ("basicly-bot", "bot@example.com")


def test_main_blocks_bot_email_violating_allow_pattern(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_identity_guard_module()
    _init_repo(tmp_path, "Human Dev", "human@company.com", allow_email=r"@company\.com$")
    rc = _run_main(
        module,
        tmp_path,
        monkeypatch,
        GIT_COMMITTER_NAME="basicly-bot",
        GIT_COMMITTER_EMAIL="bot@example.com",
    )
    assert rc == 1


def test_main_allows_bot_email_matching_allow_pattern(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_identity_guard_module()
    _init_repo(tmp_path, "Human Dev", "human@company.com", allow_email=r"@company\.com$")
    rc = _run_main(
        module,
        tmp_path,
        monkeypatch,
        GIT_AUTHOR_NAME="basicly-bot",
        GIT_AUTHOR_EMAIL="bot@company.com",
        GIT_COMMITTER_NAME="basicly-bot",
        GIT_COMMITTER_EMAIL="bot@company.com",
    )
    assert rc == 0


def test_main_passes_human_commit_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_identity_guard_module()
    _init_repo(tmp_path, "Human Dev", "human@company.com", allow_email=r"@company\.com$")
    for var in (
        "GIT_AUTHOR_NAME",
        "GIT_AUTHOR_EMAIL",
        "GIT_COMMITTER_NAME",
        "GIT_COMMITTER_EMAIL",
    ):
        monkeypatch.delenv(var, raising=False)
    assert _run_main(module, tmp_path, monkeypatch) == 0
