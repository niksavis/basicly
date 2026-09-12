from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from tests.kit_resolver_helpers import (
    KIT,
    MAP,
    REPO_ROOT,
    _definition,
    _expected,
    _load_kit,
    kit,
)


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        pytest.param("---\ntier: high\n---\n\nBody.\n", "high", id="plain"),
        pytest.param('---\ntier: "high"\n---\n', "high", id="double-quoted"),
        pytest.param("---\ntier: 'high'\n---\n", "high", id="single-quoted"),
        pytest.param("---\ntier:  HIGH \n---\n", "high", id="padded-and-uppercase"),
        pytest.param("---\ntier: low # cheapest\n---\n", "low", id="trailing-comment"),
        pytest.param("\ufeff---\ntier: high\n---\n", "high", id="byte-order-mark"),
        pytest.param("---\nname: a\n---\n\ntier: high\n", None, id="in-the-body-not-frontmatter"),
        pytest.param("---\nmetadata:\n  tier: high\n---\n", None, id="nested-under-another-key"),
        pytest.param("---\nname: a\n---\n", None, id="frontmatter-without-a-tier"),
        pytest.param("tier: high\n", None, id="no-frontmatter-at-all"),
        pytest.param("---\ntier:\n---\n", None, id="empty-value"),
        pytest.param("", None, id="empty-file"),
    ],
)
def test_declared_tier_reads_only_a_top_level_frontmatter_scalar(
    tmp_path: Path, body: str, expected: str | None
) -> None:
    path = tmp_path / "definition.md"
    path.write_text(body, encoding="utf-8")
    assert kit.declared_tier(path) == expected


def test_declared_tier_stops_before_reading_an_unbounded_body(tmp_path: Path) -> None:

    path = tmp_path / "definition.md"
    filler = "\n".join(f"line {index}" for index in range(1000))
    path.write_text(f"---\nname: a\n{filler}\ntier: high\n", encoding="utf-8")
    assert kit.declared_tier(path) is None


def test_declared_tier_survives_bytes_that_are_not_utf8(tmp_path: Path) -> None:
    path = tmp_path / "definition.md"
    path.write_bytes(b"---\ndescription: caf\xe9\ntier: high\n---\n")
    assert kit.declared_tier(path) == "high"


def test_find_definition_locates_a_project_level_claude_agent(tmp_path: Path) -> None:
    expected = _definition(tmp_path / ".claude" / "agents" / "my-own-agent.md", tier="high")
    assert kit.find_definition("my-own-agent", "claude", roots=[tmp_path]) == expected


def test_find_definition_locates_a_copilot_agent_file(tmp_path: Path) -> None:
    expected = _definition(tmp_path / ".github" / "agents" / "my-own-agent.agent.md", tier="high")
    assert kit.find_definition("my-own-agent", "copilot", roots=[tmp_path]) == expected
    shared = _definition(tmp_path / ".claude" / "agents" / "shared.md", tier="high")
    assert kit.find_definition("shared", "copilot", roots=[tmp_path]) == shared


def test_find_definition_finds_nothing_for_a_host_with_no_definition_files() -> None:
    assert kit.find_definition("my-own-agent", "codex", roots=[REPO_ROOT]) is None


@pytest.mark.parametrize(
    ("name", "decoy"),
    [
        pytest.param("../../escaped", "escaped.md", id="parent-traversal"),
        pytest.param("nested/escaped", ".claude/agents/nested/escaped.md", id="posix-separator"),
        pytest.param(
            "nested\\escaped", ".claude/agents/nested\\escaped.md", id="windows-separator"
        ),
        pytest.param(".hidden", ".claude/agents/.hidden.md", id="leading-dot"),
        pytest.param("", ".claude/agents/.md", id="empty"),
        pytest.param("has space", ".claude/agents/has space.md", id="space"),
    ],
)
def test_find_definition_refuses_a_name_that_is_not_an_agent_slug(
    tmp_path: Path, name: str, decoy: str
) -> None:

    control = _definition(tmp_path / ".claude" / "agents" / "legit.md", tier="high")
    assert kit.find_definition("legit", "claude", roots=[tmp_path]) == control
    _definition(tmp_path / decoy, tier="high")
    assert kit.find_definition(name, "claude", roots=[tmp_path]) is None


def test_find_definition_falls_back_to_the_user_level_root(tmp_path: Path) -> None:
    user = tmp_path / "home"
    project = tmp_path / "project"
    user_agent = _definition(user / ".claude" / "agents" / "my-own-agent.md", tier="low")
    assert kit.find_definition("my-own-agent", "claude", roots=[project, user]) == user_agent
    project_agent = _definition(project / ".claude" / "agents" / "my-own-agent.md", tier="high")
    assert kit.find_definition("my-own-agent", "claude", roots=[project, user]) == project_agent


def test_find_definition_tolerates_an_undeterminable_home_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    def no_home() -> Path:
        raise RuntimeError("could not determine home directory")

    monkeypatch.setattr(Path, "home", staticmethod(no_home))
    monkeypatch.chdir(tmp_path)
    expected = _definition(tmp_path / ".claude" / "agents" / "my-own-agent.md", tier="high")
    assert kit.find_definition("my-own-agent", "claude") == expected
    assert kit.find_definition("absent-agent", "claude") is None


def test_find_map_walks_up_to_the_repository_being_worked_in(tmp_path: Path) -> None:
    installed = tmp_path / ".basicly" / "core" / "models" / MAP.name
    installed.parent.mkdir(parents=True)
    shutil.copy2(MAP, installed)
    nested = tmp_path / "src" / "deep"
    nested.mkdir(parents=True)
    assert kit.find_map(nested) == installed


def test_find_map_falls_back_to_the_map_beside_the_kit(tmp_path: Path) -> None:
    assert kit.find_map(tmp_path) == MAP


def test_the_two_files_resolve_from_a_flat_copy_anywhere(tmp_path: Path) -> None:
    flat = tmp_path / "flat"
    flat.mkdir()
    shutil.copy2(KIT, flat / KIT.name)
    shutil.copy2(MAP, flat / MAP.name)
    elsewhere = tmp_path / "unrelated"
    elsewhere.mkdir()
    copied = _load_kit(flat / KIT.name)
    assert copied.find_map(elsewhere) == flat / MAP.name
    resolver = copied.TierResolver.discover(elsewhere)
    assert resolver is not None
    assert resolver.resolve("claude", tier="high").model == _expected(
        "high", "anthropic", "anthropic"
    )
