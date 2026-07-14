"""Tests for the git-hook projection engine."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from basicly.hooks import (
    HookSpec,
    check_hooks,
    hook_stages,
    install_hooks,
    load_hook_specs,
    merge_precommit_config,
    missing_hook_installations,
    sync_hooks,
)

CORE_HOOKS_DIR = Path(".basicly/core/hooks")
REPO_ROOT = Path(__file__).parent.parent


def _local_hook_ids(config: dict) -> set[str]:
    ids: set[str] = set()
    for repo in config.get("repos", []):
        if repo.get("repo") == "local":
            ids.update(hook["id"] for hook in repo.get("hooks", []))
    return ids


def test_manifest_lists_every_catalog_hook() -> None:
    """The bundled manifest resolves to the dogfooded hook scripts."""
    specs = load_hook_specs()
    ids = {spec.id for spec in specs}
    assert ids == {
        "identity-guard",
        "pre-commit-script",
        "catalog-lint",
        "commit-msg-script",
        "beads-commit-msg-script",
        "pre-push-script",
    }


def test_manifest_ships_identity_guard_at_pre_commit() -> None:
    """identity-guard is a distributed pre-commit gate, not just hand-wired here."""
    specs = load_hook_specs()
    guard = next(spec for spec in specs if spec.id == "identity-guard")
    assert guard.script == "identity-guard.py"
    assert guard.stage == "pre-commit"
    assert guard.always_run is True


def test_merge_preserves_foreign_hooks_and_is_idempotent() -> None:
    """Merging keeps unrelated repos/hooks and re-merging is a no-op."""
    specs = [HookSpec(id="pre-commit-script", script="pre-commit.py", stage="pre-commit")]
    existing = {
        "repos": [
            {
                "repo": "https://github.com/astral-sh/ruff-pre-commit",
                "rev": "v0.1",
                "hooks": [{"id": "ruff"}],
            },
            {"repo": "local", "hooks": [{"id": "my-own-hook", "entry": "echo hi"}]},
        ]
    }
    merged = merge_precommit_config(existing, specs, CORE_HOOKS_DIR.as_posix())

    # Foreign repo untouched; the consumer's own local hook survives.
    assert any(r.get("repo", "").endswith("ruff-pre-commit") for r in merged["repos"])
    assert "my-own-hook" in _local_hook_ids(merged)
    assert "pre-commit-script" in _local_hook_ids(merged)

    remerged = merge_precommit_config(merged, specs, CORE_HOOKS_DIR.as_posix())
    assert remerged == merged


def test_sync_hooks_scaffolds_and_check_round_trips(tmp_path: Path) -> None:
    """hooks-build materializes scripts + wiring; hooks-check then passes."""
    result = sync_hooks(tmp_path, CORE_HOOKS_DIR)
    assert result.written

    config = tmp_path / ".pre-commit-config.yaml"
    assert config.is_file()
    for script in ("pre-commit.py", "commit-msg.py", "beads-commit-msg.py", "pre-push.py"):
        assert (tmp_path / CORE_HOOKS_DIR / script).is_file()

    loaded = yaml.safe_load(config.read_text(encoding="utf-8"))
    assert "pre-push-script" in _local_hook_ids(loaded)

    assert check_hooks(tmp_path, CORE_HOOKS_DIR) == []

    # A second build changes nothing.
    again = sync_hooks(tmp_path, CORE_HOOKS_DIR)
    assert again.written == []


def test_check_detects_wiring_drift(tmp_path: Path) -> None:
    """Removing a managed hook from the config is reported as stale."""
    sync_hooks(tmp_path, CORE_HOOKS_DIR)
    config = tmp_path / ".pre-commit-config.yaml"

    data = yaml.safe_load(config.read_text(encoding="utf-8"))
    for repo in data["repos"]:
        if repo.get("repo") == "local":
            repo["hooks"] = [h for h in repo["hooks"] if h["id"] != "pre-push-script"]
    config.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    mismatches = check_hooks(tmp_path, CORE_HOOKS_DIR)
    assert any(reason == "managed hook 'pre-push-script' missing" for _, reason in mismatches)


def test_hook_entry_quotes_paths_with_spaces() -> None:
    """A core path containing a space must survive pre-commit's shell-split."""
    specs = [HookSpec(id="pre-commit-script", script="pre-commit.py", stage="pre-commit")]
    merged = merge_precommit_config(None, specs, "agent config/hooks")
    entry = merged["repos"][0]["hooks"][0]["entry"]
    assert entry == "uv run python 'agent config/hooks/pre-commit.py'"
    # A plain path stays unquoted, keeping the dogfooded config stable.
    plain = merge_precommit_config(None, specs, CORE_HOOKS_DIR.as_posix())
    assert plain["repos"][0]["hooks"][0]["entry"] == (
        "uv run python .basicly/core/hooks/pre-commit.py"
    )


def test_dogfood_config_passes_check() -> None:
    """This repo's own hand-authored config must satisfy its own gate.

    Regression: check_hooks used to compare full file text against a
    yaml.safe_dump re-render, so the dogfooded 4-block, hand-formatted config
    was permanently reported stale.
    """
    assert check_hooks(REPO_ROOT, CORE_HOOKS_DIR) == []


def test_semantically_synced_config_is_left_untouched(tmp_path: Path) -> None:
    """Comments and formatting survive when managed hooks are already in sync."""
    sync_hooks(tmp_path, CORE_HOOKS_DIR)
    config = tmp_path / ".pre-commit-config.yaml"

    # Reformat by hand: prepend a comment the consumer cares about.
    commented = "# pinned for CVE-2024-1234\n" + config.read_text(encoding="utf-8")
    config.write_text(commented, encoding="utf-8")

    result = sync_hooks(tmp_path, CORE_HOOKS_DIR)
    assert result.written == []
    assert config.read_text(encoding="utf-8") == commented
    assert check_hooks(tmp_path, CORE_HOOKS_DIR) == []


def test_out_of_sync_managed_hook_triggers_rewrite(tmp_path: Path) -> None:
    """A tampered managed entry is detected and repaired by a rebuild."""
    sync_hooks(tmp_path, CORE_HOOKS_DIR)
    config = tmp_path / ".pre-commit-config.yaml"

    data = yaml.safe_load(config.read_text(encoding="utf-8"))
    for repo in data["repos"]:
        if repo.get("repo") == "local":
            for hook in repo["hooks"]:
                if hook["id"] == "pre-push-script":
                    hook["entry"] = "echo tampered"
    config.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    mismatches = check_hooks(tmp_path, CORE_HOOKS_DIR)
    assert any(reason == "managed hook 'pre-push-script' out of sync" for _, reason in mismatches)

    result = sync_hooks(tmp_path, CORE_HOOKS_DIR)
    assert config in result.written
    assert check_hooks(tmp_path, CORE_HOOKS_DIR) == []


def test_hook_stages_returns_distinct_stages_in_order() -> None:
    """hook_stages collapses per-hook stages to distinct values, first-seen order."""
    specs = [
        HookSpec(id="a", script="a.py", stage="pre-commit"),
        HookSpec(id="b", script="b.py", stage="commit-msg"),
        HookSpec(id="c", script="c.py", stage="commit-msg"),
        HookSpec(id="d", script="d.py", stage="pre-push"),
    ]
    assert hook_stages(specs) == ["pre-commit", "commit-msg", "pre-push"]


def test_missing_hook_installations_detects_uninstalled_and_unmanaged(tmp_path: Path) -> None:
    """A stage is 'installed' only when a pre-commit dispatcher exists for it."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)  # nosec B603 B607
    hooks_dir = tmp_path / ".git" / "hooks"
    # pre-commit: a real pre-commit dispatcher (has the marker) -> installed.
    (hooks_dir / "pre-commit").write_text("#!/usr/bin/env bash\n# pre-commit\n", encoding="utf-8")
    # pre-push: some foreign hook without the marker -> not installed.
    (hooks_dir / "pre-push").write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
    # commit-msg: absent -> not installed.

    missing = missing_hook_installations(tmp_path, ["pre-commit", "commit-msg", "pre-push"])
    assert missing == ["commit-msg", "pre-push"]


def test_install_hooks_returns_guidance_when_precommit_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When neither pre-commit nor uv is on PATH, install_hooks guides rather than raises."""
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    ok, message = install_hooks(tmp_path, ["pre-commit", "commit-msg", "pre-push"])
    assert ok is False
    assert "pre-commit install --install-hooks -t pre-commit -t commit-msg -t pre-push" in message
