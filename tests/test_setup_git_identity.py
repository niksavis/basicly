from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _load_module():
    """Load the setup-git-identity script module from its path."""
    script_path = Path(__file__).resolve().parents[1] / ".scripts" / "setup_git_identity.py"
    spec = importlib.util.spec_from_file_location("setup_git_identity", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_sanitize_label_makes_a_safe_slug() -> None:
    """Hosts become filesystem-safe, hyphen-separated labels."""
    module = _load_module()
    assert module.sanitize_label("github.com") == "github-com"
    assert module.sanitize_label("bitbucket.drei.com") == "bitbucket-drei-com"


def test_sanitize_label_rejects_empty() -> None:
    """A host with no usable characters raises rather than producing an empty label."""
    module = _load_module()
    with pytest.raises(ValueError):
        module.sanitize_label("...")


def test_url_glob_and_condition() -> None:
    """The URL glob and includeIf condition are built from the host."""
    module = _load_module()
    glob = module.host_url_glob("github.com")
    assert glob == "https://github.com/**"
    assert module.includeif_condition(glob) == "hasconfig:remote.*.url:https://github.com/**"
