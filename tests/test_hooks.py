"""Tests for the git-hook projection engine."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from basicly import hooks as hooks_module
from basicly.hooks import (
    HookSpec,
    check_copilot_hooks,
    check_hooks,
    claude_hook_specs,
    git_hook_specs,
    hook_stages,
    install_hooks,
    load_hook_specs,
    merge_precommit_config,
    missing_hook_installations,
    remove_copilot_hooks,
    render_precommit_config,
    selected_hook_specs,
    sync_copilot_hooks,
    sync_hooks,
)
from basicly.schema import ValidationError

CORE_HOOKS_DIR = Path(".basicly/core/hooks")
REPO_ROOT = Path(__file__).parent.parent


def _materialize_hooks(tmp_path: Path) -> None:
    """Copy the catalog hook scripts the way `basicly install` would."""
    shutil.copytree(REPO_ROOT / CORE_HOOKS_DIR, tmp_path / CORE_HOOKS_DIR)


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
        "secret-scan",
        "commit-msg-script",
        "beads-commit-msg-script",
        "pre-push-script",
        "protect-generated",
        "protect-generated-commit",
        "tool-usage",
        "tool-usage-copilot",
    }


def test_manifest_ships_identity_guard_at_pre_commit() -> None:
    """identity-guard is a distributed pre-commit gate, not just hand-wired here."""
    specs = load_hook_specs()
    guard = next(spec for spec in specs if spec.id == "identity-guard")
    assert guard.script == "identity-guard.py"
    assert guard.stage == "pre-commit"
    assert guard.always_run is True


def test_manifest_ships_protect_generated_for_claude() -> None:
    """The generated-files guard targets the Claude agent-hook manager, not git."""
    specs = load_hook_specs()
    guard = next(spec for spec in specs if spec.id == "protect-generated")
    assert guard.script == "protect-generated.py"
    assert guard.manager == "claude"
    assert git_hook_specs(specs) == [s for s in specs if s.manager == "git"]
    assert guard in claude_hook_specs(specs)


def test_manifest_ships_protect_generated_commit_for_git() -> None:
    """The commit-time backstop is a git pre-commit gate for all agents (basicly-yw28)."""
    specs = load_hook_specs()
    backstop = next(spec for spec in specs if spec.id == "protect-generated-commit")
    assert backstop.script == "protect-generated-commit.py"
    assert backstop.stage == "pre-commit"
    assert backstop.manager == "git"
    assert backstop in git_hook_specs(specs)


def test_copilot_hooks_sync_check_and_remove_roundtrip(tmp_path: Path) -> None:
    """The copilot manager writes .github/hooks/basicly-*.json; check and remove agree."""
    result = sync_copilot_hooks(tmp_path, CORE_HOOKS_DIR)
    hook_file = tmp_path / ".github/hooks/basicly-tool-usage-copilot.json"
    assert hook_file in result.written

    config = json.loads(hook_file.read_text(encoding="utf-8"))
    assert config["version"] == 1
    entry = config["hooks"]["postToolUse"][0]
    assert entry["type"] == "command"
    assert entry["bash"] == "uv run python .basicly/core/hooks/tool-usage.py"
    assert "tool-usage.py" in entry["powershell"]

    assert check_copilot_hooks(tmp_path, CORE_HOOKS_DIR) == []
    again = sync_copilot_hooks(tmp_path, CORE_HOOKS_DIR)
    assert again.written == []

    # A stale managed file (not in the catalog) is flagged and pruned on sync.
    stray = tmp_path / ".github/hooks/basicly-retired.json"
    stray.write_text("{}\n", encoding="utf-8")
    assert any(
        "stale managed" in reason for _, reason in check_copilot_hooks(tmp_path, CORE_HOOKS_DIR)
    )
    sync_copilot_hooks(tmp_path, CORE_HOOKS_DIR)
    assert not stray.exists()

    # A consumer's own hook file survives uninstall; managed files do not.
    foreign = tmp_path / ".github/hooks/my-own.json"
    foreign.write_text("{}\n", encoding="utf-8")
    assert remove_copilot_hooks(tmp_path) == 1
    assert foreign.exists() and not hook_file.exists()


def test_manifest_ships_tool_usage_for_both_agent_managers() -> None:
    """The usage counter targets Claude PostToolUse (Bash) and Copilot postToolUse."""
    specs = load_hook_specs()
    claude = next(spec for spec in specs if spec.id == "tool-usage")
    assert (claude.manager, claude.stage, claude.matcher) == ("claude", "posttooluse", "Bash|Skill")
    copilot = next(spec for spec in specs if spec.id == "tool-usage-copilot")
    assert (copilot.manager, copilot.stage) == ("copilot", "posttooluse")
    assert copilot.script == claude.script == "tool-usage.py"


def test_load_rejects_unknown_manager(tmp_path: Path) -> None:
    """A manifest entry with a manager basicly cannot render fails the load."""
    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir()
    (hooks_dir / "hooks.yaml").write_text(
        "hooks:\n  - id: x\n    script: x.py\n    stage: pre-commit\n    manager: lefthook\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unknown manager"):
        load_hook_specs(hooks_dir)


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
    """With a materialized core, hooks-build writes wiring; hooks-check passes."""
    _materialize_hooks(tmp_path)
    result = sync_hooks(tmp_path, CORE_HOOKS_DIR)
    assert result.written

    config = tmp_path / ".pre-commit-config.yaml"
    assert config.is_file()
    for script in ("pre-commit.py", "commit-msg.py", "beads-commit-msg.py", "pre-push.py"):
        assert (tmp_path / CORE_HOOKS_DIR / script).is_file()

    loaded = yaml.safe_load(config.read_text(encoding="utf-8"))
    assert "pre-push-script" in _local_hook_ids(loaded)
    # Agent-managed hooks never reach the pre-commit config.
    assert "protect-generated" not in _local_hook_ids(loaded)

    assert check_hooks(tmp_path, CORE_HOOKS_DIR) == []

    # A second build changes nothing.
    again = sync_hooks(tmp_path, CORE_HOOKS_DIR)
    assert again.written == []


def test_sync_hooks_requires_materialized_core(tmp_path: Path) -> None:
    """Without a materialized core, hooks-build refuses and points at install."""
    with pytest.raises(ValidationError, match="basicly install"):
        sync_hooks(tmp_path, CORE_HOOKS_DIR)


def test_selected_hook_specs_filters_tagged_specs() -> None:
    """Untagged specs are universal; tagged ones need selection overlap."""
    universal = HookSpec(id="a", script="a.py", stage="pre-commit")
    tagged = HookSpec(id="b", script="b.py", stage="pre-commit", technologies=("node",))
    specs = [universal, tagged]
    assert selected_hook_specs(specs, None) == specs
    assert selected_hook_specs(specs, frozenset({"node"})) == specs
    assert selected_hook_specs(specs, frozenset({"python"})) == [universal]


def test_sync_hooks_prunes_hook_excluded_by_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Narrowing the selection rewrites the config without the excluded hook."""
    _materialize_hooks(tmp_path)
    tagged = HookSpec(
        id="uv-lock-check", script="uv-lock.py", stage="pre-commit", technologies=("python",)
    )
    real_specs = load_hook_specs()
    monkeypatch.setattr(hooks_module, "load_hook_specs", lambda *_a, **_k: [*real_specs, tagged])

    sync_hooks(tmp_path, CORE_HOOKS_DIR)
    config = tmp_path / ".pre-commit-config.yaml"
    assert "uv-lock-check" in _local_hook_ids(yaml.safe_load(config.read_text(encoding="utf-8")))

    selection = frozenset({"zsh"})
    mismatches = check_hooks(tmp_path, CORE_HOOKS_DIR, selection)
    assert any("excluded by technology selection" in reason for _, reason in mismatches)

    result = sync_hooks(tmp_path, CORE_HOOKS_DIR, selection)
    assert result.written == [config]
    assert "uv-lock-check" not in _local_hook_ids(
        yaml.safe_load(config.read_text(encoding="utf-8"))
    )
    assert check_hooks(tmp_path, CORE_HOOKS_DIR, selection) == []


def test_check_detects_wiring_drift(tmp_path: Path) -> None:
    """Removing a managed hook from the config is reported as stale."""
    _materialize_hooks(tmp_path)
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


def test_semantically_synced_config_is_left_untouched(tmp_path: Path) -> None:
    """Comments and formatting survive when managed hooks are already in sync."""
    _materialize_hooks(tmp_path)
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
    _materialize_hooks(tmp_path)
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
        HookSpec(id="e", script="e.py", stage="pretooluse", manager="claude"),
    ]
    # Agent-hook stages must never reach `pre-commit install -t`.
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


def test_missing_hook_installations_degrades_when_git_is_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Git not on PATH must fall back to <repo>/.git/hooks, not raise (status exits 0)."""

    def _no_git(*_args: object, **_kwargs: object) -> object:
        raise FileNotFoundError(2, "The system cannot find the file specified")

    monkeypatch.setattr(hooks_module.subprocess, "run", _no_git)
    hooks_dir = tmp_path / ".git" / "hooks"
    hooks_dir.mkdir(parents=True)
    (hooks_dir / "pre-commit").write_text("# pre-commit\n", encoding="utf-8")

    # Resolves via the .git/hooks fallback instead of propagating the OSError.
    assert missing_hook_installations(tmp_path, ["pre-commit", "pre-push"]) == ["pre-push"]


def test_install_hooks_returns_guidance_when_precommit_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When neither pre-commit nor uv is on PATH, install_hooks guides rather than raises."""
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    ok, message = install_hooks(tmp_path, ["pre-commit", "commit-msg", "pre-push"])
    assert ok is False
    assert "pre-commit install --install-hooks -t pre-commit -t commit-msg -t pre-push" in message
