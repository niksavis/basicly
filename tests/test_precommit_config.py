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

    assert any(r.get("repo", "").endswith("ruff-pre-commit") for r in merged["repos"])
    assert "my-own-hook" in _local_hook_ids(merged)
    assert "pre-commit-script" in _local_hook_ids(merged)

    remerged = merge_precommit_config(merged, specs, CORE_HOOKS_DIR.as_posix())
    assert remerged == merged


def test_hook_entry_quotes_paths_with_spaces() -> None:
    specs = [HookSpec(id="pre-commit-script", script="pre-commit.py", stage="pre-commit")]
    merged = merge_precommit_config(None, specs, "agent config/hooks")
    entry = merged["repos"][0]["hooks"][0]["entry"]
    assert entry == "uv run --no-project python 'agent config/hooks/pre-commit.py'"
    plain = merge_precommit_config(None, specs, CORE_HOOKS_DIR.as_posix())
    assert plain["repos"][0]["hooks"][0]["entry"] == (
        "uv run --no-project python .basicly/core/hooks/pre-commit.py"
    )


def test_rewrite_preserves_unmanaged_hook_comments() -> None:

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

    assert "# Repo-wide markdownlint through the config file; keep this note." in rendered
    assert "npx --no-install markdownlint-cli2" in rendered
    assert "files:" in rendered
    assert rendered.index("markdownlint") < rendered.index("pre-commit-script")
    loaded = yaml.safe_load(rendered)
    assert "markdownlint" in _local_hook_ids(loaded)
    assert "pre-commit-script" in _local_hook_ids(loaded)
    assert render_precommit_config(rendered, specs, CORE_HOOKS_DIR.as_posix()) == rendered
