"""Tests for the .pre-commit-config.yaml document basicly co-owns."""

from __future__ import annotations

from pathlib import Path

import yaml

from basicly.hooks import HookSpec
from basicly.precommit_config import merge_precommit_config, render_precommit_config

CORE_HOOKS_DIR = Path(".basicly/core/hooks")


def _local_hook_ids(config: dict) -> set[str]:
    ids: set[str] = set()
    for repo in config.get("repos", []):
        if repo.get("repo") == "local":
            ids.update(hook["id"] for hook in repo.get("hooks", []))
    return ids


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


def test_rewrite_preserves_unmanaged_hook_comments() -> None:
    """A consumer's own hook and its explanatory comments survive a rewrite.

    Regression (basicly-wd7u): render_precommit_config round-tripped the whole
    file through yaml.safe_load/safe_dump, which dropped comments and hoisted a
    hand-maintained hook. Now only basicly's managed block is rebuilt; every
    unmanaged repo/hook keeps its comments and stays ahead of the managed
    block.
    """
    existing = (
        "repos:\n"
        "  # Repo-wide markdownlint through the config file; keep this note.\n"
        "  - repo: local\n"
        "    hooks:\n"
        "      - id: markdownlint\n"
        "        name: markdownlint\n"
        "        entry: npx --no-install markdownlint-cli2\n"
        "        language: system\n"
        "        files: \\.md$\n"
    )
    specs = [HookSpec(id="pre-commit-script", script="pre-commit.py", stage="pre-commit")]
    rendered = render_precommit_config(existing, specs, CORE_HOOKS_DIR.as_posix())

    # The explanatory comment survives verbatim (the reported regression).
    assert "# Repo-wide markdownlint through the config file; keep this note." in rendered
    # The unmanaged hook and its non-default key survive.
    assert "npx --no-install markdownlint-cli2" in rendered
    assert "files:" in rendered
    # It stays ahead of basicly's appended managed block (no hoisting).
    assert rendered.index("markdownlint") < rendered.index("pre-commit-script")
    # The managed hook was added and both are seen as local hooks.
    loaded = yaml.safe_load(rendered)
    assert "markdownlint" in _local_hook_ids(loaded)
    assert "pre-commit-script" in _local_hook_ids(loaded)
    # Re-rendering the output is a no-op (idempotent).
    assert render_precommit_config(rendered, specs, CORE_HOOKS_DIR.as_posix()) == rendered
