from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest


def _load_identity_guard_module():
    """Load the identity-guard hook module from its script path."""
    script_path = (
        Path(__file__).resolve().parents[2] / ".basicly" / "core" / "hooks" / "identity-guard.py"
    )
    spec = importlib.util.spec_from_file_location("identity_guard_hook", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_accepts_a_real_identity() -> None:
    """A configured name and real email should pass."""
    module = _load_identity_guard_module()
    ok, _ = module.check_identity("Niksa Visic", "niksavis@live.com")
    assert ok


def test_rejects_missing_email() -> None:
    """An empty email (git would use the hostname fallback) must be blocked."""
    module = _load_identity_guard_module()
    ok, message = module.check_identity("Niksa Visic", "")
    assert not ok
    assert "user.email" in message


def test_rejects_hostname_fallback_email() -> None:
    """A machine-local auto-generated email must be blocked."""
    module = _load_identity_guard_module()
    for bad in ("visicni@at-work.local", "user@host.(none)", "user@box.localdomain"):
        ok, message = module.check_identity("Niksa Visic", bad)
        assert not ok, bad
        assert "auto-generated" in message


def test_rejects_missing_name() -> None:
    """An empty name must be blocked even when the email is valid."""
    module = _load_identity_guard_module()
    ok, message = module.check_identity("", "niksavis@live.com")
    assert not ok
    assert "user.name" in message


def test_allow_email_pattern_enforced_when_set() -> None:
    """When basicly.identityAllowEmail is set, a non-matching email is blocked."""
    module = _load_identity_guard_module()
    ok_match, _ = module.check_identity("Niksa Visic", "niksa.visic@drei.com", r"@drei\.com$")
    assert ok_match
    ok_miss, message = module.check_identity("Niksa Visic", "niksavis@live.com", r"@drei\.com$")
    assert not ok_miss
    assert "identityAllowEmail" in message


# --- effective (env-aware) identity: opt-in bot identity (basicly-smzg) ------


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
    """Resolve the GIT_*_EMAIL env override via git var, not just config (basicly-smzg)."""
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
    """A bot committer email that fails allow-email is blocked though config passes."""
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
    """A bot identity whose email matches the allow pattern passes both checks."""
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
    """No env override: behavior is exactly the config-only check (backward compatible)."""
    module = _load_identity_guard_module()
    _init_repo(tmp_path, "Human Dev", "human@company.com", allow_email=r"@company\.com$")
    # Clear any inherited GIT_* identity so this is a pure config commit.
    for var in (
        "GIT_AUTHOR_NAME",
        "GIT_AUTHOR_EMAIL",
        "GIT_COMMITTER_NAME",
        "GIT_COMMITTER_EMAIL",
    ):
        monkeypatch.delenv(var, raising=False)
    assert _run_main(module, tmp_path, monkeypatch) == 0
