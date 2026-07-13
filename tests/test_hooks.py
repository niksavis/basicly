"""Tests for the git-hook projection engine."""

from __future__ import annotations

from pathlib import Path

import yaml

from basicly.hooks import (
    HookSpec,
    check_hooks,
    load_hook_specs,
    merge_precommit_config,
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
    """The bundled manifest resolves to the four dogfooded hook scripts."""
    specs = load_hook_specs()
    ids = {spec.id for spec in specs}
    assert ids == {
        "pre-commit-script",
        "commit-msg-script",
        "beads-commit-msg-script",
        "pre-push-script",
    }


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
