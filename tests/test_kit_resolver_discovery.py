"""How the tier resolver kit finds what it reads.

Split from the original suite by the module-size ratchet (basicly-u2hl.36).
Reading a declared tier off a definition, locating that definition from a
subagent name, and finding the map itself are one responsibility: every one of
them answers "where does the input come from", and all three fail closed.
"""

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

# --- reading the declared tier off the definition -----------------------------


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
    """What counts as a declared tier, and what deliberately does not."""
    path = tmp_path / "definition.md"
    path.write_text(body, encoding="utf-8")
    assert kit.declared_tier(path) == expected


def test_declared_tier_stops_before_reading_an_unbounded_body(tmp_path: Path) -> None:
    """An unclosed fence must not make the scanner read a whole long prompt.

    A thousand lines is past any frontmatter cap the module could reasonably
    use, so this pins the bound existing rather than its exact value.
    """
    path = tmp_path / "definition.md"
    filler = "\n".join(f"line {index}" for index in range(1000))
    path.write_text(f"---\nname: a\n{filler}\ntier: high\n", encoding="utf-8")
    assert kit.declared_tier(path) is None


def test_declared_tier_survives_bytes_that_are_not_utf8(tmp_path: Path) -> None:
    """A definition with an undecodable byte still yields its tier, not a crash."""
    path = tmp_path / "definition.md"
    path.write_bytes(b"---\ndescription: caf\xe9\ntier: high\n---\n")
    assert kit.declared_tier(path) == "high"


# --- finding a definition by subagent name ------------------------------------


def test_find_definition_locates_a_project_level_claude_agent(tmp_path: Path) -> None:
    """The path claude reads a project agent from."""
    expected = _definition(tmp_path / ".claude" / "agents" / "my-own-agent.md", tier="high")
    assert kit.find_definition("my-own-agent", "claude", roots=[tmp_path]) == expected


def test_find_definition_locates_a_copilot_agent_file(tmp_path: Path) -> None:
    """Copilot's own suffix, plus the claude directory VS Code also reads."""
    expected = _definition(tmp_path / ".github" / "agents" / "my-own-agent.agent.md", tier="high")
    assert kit.find_definition("my-own-agent", "copilot", roots=[tmp_path]) == expected
    shared = _definition(tmp_path / ".claude" / "agents" / "shared.md", tier="high")
    assert kit.find_definition("shared", "copilot", roots=[tmp_path]) == shared


def test_find_definition_finds_nothing_for_a_host_with_no_definition_files() -> None:
    """Codex has no per-agent file, so a tier for it can only be argued or defaulted."""
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
    """The name comes from the host's tool input, so it is validated, not joined.

    Each rejected name gets a decoy file it *would* reach if the name were joined
    onto the path unchecked, and a legitimate agent is looked up first so the
    directories the traversal needs really exist. Without both, a refusal and a
    plain miss look identical and the test passes on an unvalidated resolver —
    which is exactly what a mutation run showed before the decoys were added.
    The separator cases carry their own platform difference as data: one path
    string names a nested file on Windows and a literal filename on POSIX, and
    both are created, so neither platform is the one that skips.
    """
    control = _definition(tmp_path / ".claude" / "agents" / "legit.md", tier="high")
    assert kit.find_definition("legit", "claude", roots=[tmp_path]) == control
    _definition(tmp_path / decoy, tier="high")
    assert kit.find_definition(name, "claude", roots=[tmp_path]) is None


def test_find_definition_falls_back_to_the_user_level_root(tmp_path: Path) -> None:
    """A user-level agent resolves when the project has none, project first."""
    user = tmp_path / "home"
    project = tmp_path / "project"
    user_agent = _definition(user / ".claude" / "agents" / "my-own-agent.md", tier="low")
    assert kit.find_definition("my-own-agent", "claude", roots=[project, user]) == user_agent
    project_agent = _definition(project / ".claude" / "agents" / "my-own-agent.md", tier="high")
    assert kit.find_definition("my-own-agent", "claude", roots=[project, user]) == project_agent


def test_find_definition_tolerates_an_undeterminable_home_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A hook in a context with no home must miss, not raise.

    ``Path.home()`` raises when neither the environment nor the password
    database can answer — a real state for a service or container invocation,
    and one no platform-specific skip can cover.
    """

    def no_home() -> Path:
        raise RuntimeError("could not determine home directory")

    monkeypatch.setattr(Path, "home", staticmethod(no_home))
    monkeypatch.chdir(tmp_path)
    expected = _definition(tmp_path / ".claude" / "agents" / "my-own-agent.md", tier="high")
    assert kit.find_definition("my-own-agent", "claude") == expected
    assert kit.find_definition("absent-agent", "claude") is None


# --- finding the map ----------------------------------------------------------


def test_find_map_walks_up_to_the_repository_being_worked_in(tmp_path: Path) -> None:
    """What lets one machine-wide hook answer for whichever repo it runs in."""
    installed = tmp_path / ".basicly" / "core" / "models" / MAP.name
    installed.parent.mkdir(parents=True)
    shutil.copy2(MAP, installed)
    nested = tmp_path / "src" / "deep"
    nested.mkdir(parents=True)
    assert kit.find_map(nested) == installed


def test_find_map_falls_back_to_the_map_beside_the_kit(tmp_path: Path) -> None:
    """A kit installed outside a repo still has its own committed neighbour."""
    assert kit.find_map(tmp_path) == MAP


def test_the_two_files_resolve_from_a_flat_copy_anywhere(tmp_path: Path) -> None:
    """The plug-and-play claim: copy the module and the map into one directory."""
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
