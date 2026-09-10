"""A managed hook that declares a path scope (basicly-lc2bd3v).

`catalog-lint` was `always_run`, so a validation bug in it blocked commits touching no
catalog file — reported from a consumer whose commit changed a Lua template, two docs
and a test. `always_run` overrides `files` in pre-commit, so a spec declares one or the
other, and the live-tree test below is what keeps the two from being declared together.
"""

from __future__ import annotations

import re
from pathlib import Path

from basicly.hooks import HookSpec, load_hook_specs
from basicly.precommit_config import merge_precommit_config

CORE_HOOKS = Path(__file__).parent.parent / ".basicly" / "core" / "hooks"


def _entry(spec: HookSpec) -> dict:
    """The rendered pre-commit entry for one spec."""
    merged = merge_precommit_config(None, [spec], ".basicly/core/hooks")
    return merged["repos"][0]["hooks"][0]


def test_a_declared_scope_reaches_the_rendered_config() -> None:
    """Without this the field parses and is silently dropped, which reads as scoped."""
    spec = HookSpec(id="h", script="h.py", stage="pre-commit", files="^docs/")

    assert _entry(spec)["files"] == "^docs/"


def test_no_scope_leaves_pre_commits_default() -> None:
    """The control: an absent scope must not emit `files`, which would match nothing."""
    spec = HookSpec(id="h", script="h.py", stage="pre-commit", always_run=True)

    entry = _entry(spec)
    assert "files" not in entry
    assert entry["always_run"] is True


def test_catalog_lint_is_scoped_and_not_always_run() -> None:
    """`always_run` would override the scope, restoring the blast radius silently."""
    specs = {spec.id: spec for spec in load_hook_specs(CORE_HOOKS)}
    catalog_lint = specs["catalog-lint"]

    assert catalog_lint.files, "catalog-lint declares no scope"
    assert not catalog_lint.always_run, "always_run overrides the scope it declares"


def test_the_scope_covers_the_sources_the_lint_reads() -> None:
    """A source outside the scope is a source whose defect the hook cannot see."""
    specs = {spec.id: spec for spec in load_hook_specs(CORE_HOOKS)}
    pattern = re.compile(specs["catalog-lint"].files)

    for path in (
        ".basicly/core/skills/python/skill.yaml",
        ".basicly/core/fragments/project/x.fragment.yaml",
        ".basicly-local/fragments/user/x.fragment.yaml",
        "basicly.toml",
    ):
        assert pattern.search(path), f"{path} is outside the scope"
    for path in ("README.md", "src/basicly/cli.py", "basicly.toml.bak"):
        assert not pattern.search(path), f"{path} is inside the scope"
