from __future__ import annotations

import importlib.util
from pathlib import Path


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
