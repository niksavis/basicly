"""Tests for agent source loading, composition, and lint."""

from __future__ import annotations

from pathlib import Path

import pytest

from basicly.agents import (
    AGENTS_OUTPUT_ROOTS,
    CLAUDE_AGENTS_ROOT,
    COPILOT_AGENTS_ROOT,
    GENERATED_MARKER,
    ORPHANED_REASON,
    SLOT_ORDER,
    AgentOutputRoot,
    check_synced_agents,
    compose_body,
    compose_description,
    default_agent_roots,
    discover_agents,
    discover_blocks,
    render_agent_md,
    sync_agents,
    unknown_block_refs,
)
from basicly.copilot_tools import WRITE_TOOLS, resolve_copilot_tool
from basicly.schema import ValidationError


def _write_block(root: Path, block_id: str, body: str = "Block body.", **extra: object) -> None:
    lines = [
        "schema_version: 1",
        f"id: {block_id}",
        f"description: the {block_id} block",
    ]
    for key, value in extra.items():
        lines.append(f"{key}: {value}")
    lines.append("body: |")
    lines.extend(f"  {line}" for line in body.split("\n"))
    path = root / "blocks" / f"{block_id}.block.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _agent_yaml(
    slug: str,
    *,
    tools: str = "[Read, Grep, Glob]",
    posture: str = "Read-only.",
    slots: str | None = None,
    extra: str = "",
) -> str:
    if slots is None:
        slots = "\n".join(
            f"  {name}:\n    - text: |\n        The {name} slot." for name in SLOT_ORDER
        )
    return (
        f"schema_version: 1\n"
        f"name: {slug}\n"
        f"purpose: Reviews things.\n"
        f"triggers: Use proactively after changes.\n"
        f"returns: Returns prioritized findings.\n"
        f"posture: {posture}\n"
        f"tools: {tools}\n"
        f"{extra}"
        f"slots:\n{slots}\n"
    )


def _write_agent(root: Path, slug: str, content: str | None = None) -> None:
    path = root / slug / "agent.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content if content is not None else _agent_yaml(slug), encoding="utf-8")


def _roots(tmp_path: Path) -> list[tuple[Path, str]]:
    return [(tmp_path / "core", "core"), (tmp_path / "user", "user")]


def test_discover_blocks_loads_core_blocks(tmp_path: Path) -> None:
    """Core blocks load keyed by id with stripped bodies."""
    _write_block(tmp_path / "core", "evidence", body="Cite path:line.")
    blocks = discover_blocks(_roots(tmp_path))
    assert set(blocks) == {"evidence"}
    assert blocks["evidence"].body == "Cite path:line."
    assert blocks["evidence"].source == "core"


def test_block_file_name_must_match_id(tmp_path: Path) -> None:
    """A block whose file name diverges from its id is rejected."""
    path = tmp_path / "core" / "blocks" / "wrong.block.yaml"
    path.parent.mkdir(parents=True)
    path.write_text("schema_version: 1\nid: evidence\ndescription: d\nbody: b\n", encoding="utf-8")
    with pytest.raises(ValidationError, match=r"must be named 'evidence\.block\.yaml'"):
        discover_blocks(_roots(tmp_path))


def test_overlay_block_requires_override(tmp_path: Path) -> None:
    """An overlay block shadowing a core block without override is rejected."""
    _write_block(tmp_path / "core", "evidence")
    _write_block(tmp_path / "user", "evidence")
    with pytest.raises(ValidationError, match="add 'override: true'"):
        discover_blocks(_roots(tmp_path))


def test_overlay_block_with_override_replaces_core(tmp_path: Path) -> None:
    """An overlay block with override: true replaces the core block."""
    _write_block(tmp_path / "core", "evidence", body="Core body.")
    _write_block(tmp_path / "user", "evidence", body="User body.", override="true")
    blocks = discover_blocks(_roots(tmp_path))
    assert blocks["evidence"].body == "User body."
    assert blocks["evidence"].source == "user"


def test_discover_agents_parses_full_agent(tmp_path: Path) -> None:
    """A well-formed agent parses with tools, an unset model tier, and ordered slots."""
    _write_agent(tmp_path / "core", "code-reviewer")
    agents = discover_agents(_roots(tmp_path))
    assert [agent.slug for agent in agents] == ["code-reviewer"]
    agent = agents[0]
    assert agent.tools == ("Read", "Grep", "Glob")
    assert agent.tier == ""
    assert tuple(name for name, _ in agent.slots) == SLOT_ORDER


def test_discover_agents_parses_the_model_tier(tmp_path: Path) -> None:
    """A declared model tier loads onto the definition (nothing resolves it yet)."""
    _write_agent(
        tmp_path / "core", "code-reviewer", _agent_yaml("code-reviewer", extra="tier: low\n")
    )
    (agent,) = discover_agents(_roots(tmp_path))
    assert agent.tier == "low" and agent.deprecated_model == ""


def test_agent_name_must_match_directory(tmp_path: Path) -> None:
    """An agent whose name diverges from its directory slug is rejected."""
    _write_agent(tmp_path / "core", "code-reviewer", _agent_yaml("other-name"))
    with pytest.raises(ValidationError, match="must match its directory name"):
        discover_agents(_roots(tmp_path))


def test_blocks_is_a_reserved_slug(tmp_path: Path) -> None:
    """An agent directory named 'blocks' is rejected."""
    _write_agent(tmp_path / "core", "blocks", _agent_yaml("blocks"))
    with pytest.raises(ValidationError, match="reserved for shared blocks"):
        discover_agents(_roots(tmp_path))


def test_agent_requires_explicit_tools(tmp_path: Path) -> None:
    """An empty tools list is rejected: agents never inherit every tool."""
    _write_agent(tmp_path / "core", "code-reviewer", _agent_yaml("code-reviewer", tools="[]"))
    with pytest.raises(ValidationError, match="non-empty list of tool names"):
        discover_agents(_roots(tmp_path))


def test_missing_slot_is_rejected(tmp_path: Path) -> None:
    """All five slots are required."""
    slots = "\n".join(
        f"  {name}:\n    - text: body" for name in SLOT_ORDER if name != "constraints"
    )
    _write_agent(tmp_path / "core", "code-reviewer", _agent_yaml("code-reviewer", slots=slots))
    with pytest.raises(ValidationError, match="slot 'constraints' must be a non-empty list"):
        discover_agents(_roots(tmp_path))


def test_unknown_slot_is_rejected(tmp_path: Path) -> None:
    """A slot outside the composition skeleton is rejected."""
    slots = "\n".join(f"  {name}:\n    - text: body" for name in (*SLOT_ORDER, "extras"))
    _write_agent(tmp_path / "core", "code-reviewer", _agent_yaml("code-reviewer", slots=slots))
    with pytest.raises(ValidationError, match="unknown slot"):
        discover_agents(_roots(tmp_path))


def test_slot_item_must_set_exactly_one_key(tmp_path: Path) -> None:
    """A slot item with both block and text is rejected."""
    slots = "\n".join(f"  {name}:\n    - text: body" for name in SLOT_ORDER if name != "role")
    slots = "  role:\n    - {block: b, text: t}\n" + slots
    _write_agent(tmp_path / "core", "code-reviewer", _agent_yaml("code-reviewer", slots=slots))
    with pytest.raises(ValidationError, match="exactly one of 'block' or 'text'"):
        discover_agents(_roots(tmp_path))


def test_overlay_agent_requires_override(tmp_path: Path) -> None:
    """An overlay agent shadowing a core agent without override is rejected."""
    _write_agent(tmp_path / "core", "code-reviewer")
    _write_agent(tmp_path / "user", "code-reviewer")
    with pytest.raises(ValidationError, match="add 'override: true'"):
        discover_agents(_roots(tmp_path))


def test_overlay_agent_with_override_replaces_core(tmp_path: Path) -> None:
    """An overlay agent with override: true replaces the core agent."""
    _write_agent(tmp_path / "core", "code-reviewer")
    _write_agent(
        tmp_path / "user",
        "code-reviewer",
        _agent_yaml("code-reviewer", extra="override: true\n", posture="Writes fixes."),
    )
    agents = discover_agents(_roots(tmp_path))
    assert len(agents) == 1
    assert agents[0].source == "user"
    assert agents[0].posture == "Writes fixes."


def test_compose_description_joins_four_parts(tmp_path: Path) -> None:
    """The description is the four parts joined in order."""
    _write_agent(tmp_path / "core", "code-reviewer")
    (agent,) = discover_agents(_roots(tmp_path))
    assert compose_description(agent) == (
        "Reviews things. Use proactively after changes. Returns prioritized findings. Read-only."
    )


def test_compose_body_resolves_blocks_in_slot_order(tmp_path: Path) -> None:
    """Body parts render in slot order with block refs resolved."""
    _write_block(tmp_path / "core", "honesty", body="Say so if clean.")
    slots = "\n".join(
        f"  {name}:\n    - text: {name} text" for name in SLOT_ORDER if name != "constraints"
    )
    slots += "\n  constraints:\n    - block: honesty\n    - text: Never push."
    _write_agent(tmp_path / "core", "code-reviewer", _agent_yaml("code-reviewer", slots=slots))
    (agent,) = discover_agents(_roots(tmp_path))
    body = compose_body(agent, discover_blocks(_roots(tmp_path)))
    assert body == (
        "role text\n\nstartup text\n\nprocess text\n\noutput_contract text"
        "\n\nSay so if clean.\n\nNever push."
    )


def test_compose_body_unknown_block_raises(tmp_path: Path) -> None:
    """Composing with an unresolved block ref raises."""
    slots = "\n".join(f"  {name}:\n    - text: body" for name in SLOT_ORDER if name != "role")
    slots = "  role:\n    - block: missing\n" + slots
    _write_agent(tmp_path / "core", "code-reviewer", _agent_yaml("code-reviewer", slots=slots))
    (agent,) = discover_agents(_roots(tmp_path))
    assert unknown_block_refs(agent, {}) == ["missing"]
    with pytest.raises(ValidationError, match="unknown block 'missing'"):
        compose_body(agent, {})


def _lint_repo(tmp_path: Path) -> Path:
    """Lay a repo whose core agents root is tmp_path/.basicly/core/agents."""
    return tmp_path


def test_default_agent_roots_are_core_then_overlay(tmp_path: Path) -> None:
    """Roots load core first so the overlay can override."""
    roots = default_agent_roots(tmp_path)
    assert roots == [
        (tmp_path / ".basicly/core/agents", "core"),
        (tmp_path / ".basicly-local/agents", "user"),
    ]


@pytest.mark.parametrize("root", AGENTS_OUTPUT_ROOTS, ids=lambda root: root.family)
def test_render_agent_md_shape(tmp_path: Path, root: AgentOutputRoot) -> None:
    """Frontmatter, marker, and body render in the documented shape, in every root."""
    _write_agent(tmp_path / "core", "code-reviewer")
    (agent,) = discover_agents(_roots(tmp_path))
    rendered = render_agent_md(agent, {}, root)
    lines = rendered.split("\n")
    assert lines[0] == "---"
    assert lines[1] == "name: code-reviewer"
    assert lines[2] == (
        "description: Reviews things. Use proactively after changes. "
        "Returns prioritized findings. Read-only."
    )
    assert lines[3] == "tools: Read, Grep, Glob"
    assert lines[4] == "---"
    assert lines[5] == ""
    assert lines[6] == GENERATED_MARKER
    # No family ever receives a model line: a provider model id is not portable,
    # so the tier is catalog metadata and never reaches frontmatter.
    assert "model:" not in rendered
    assert rendered.endswith("The constraints slot.\n")
    assert not rendered.endswith("\n\n")


@pytest.mark.parametrize("root", AGENTS_OUTPUT_ROOTS, ids=lambda root: root.family)
def test_render_omits_the_model_line_for_a_tier_source(
    tmp_path: Path, root: AgentOutputRoot
) -> None:
    """A declared model tier projects no `model:` (and no `tier:`) frontmatter key.

    Copilot's frontmatter has a `model` slot where Claude's does not, so this has
    to hold per root: the tier is the portable capability level and a provider
    model id never reaches any projected file (basicly-kjc5.58, basicly-8sxf).
    """
    _write_agent(
        tmp_path / "core",
        "code-reviewer",
        _agent_yaml("code-reviewer", extra="tier: maximum\n"),
    )
    (agent,) = discover_agents(_roots(tmp_path))
    rendered = render_agent_md(agent, {}, root)
    assert "model" not in rendered
    assert "tier" not in rendered
    assert "maximum" not in rendered


@pytest.mark.parametrize("root", AGENTS_OUTPUT_ROOTS, ids=lambda root: root.family)
def test_render_marker_stays_in_protect_generated_window(
    tmp_path: Path, root: AgentOutputRoot
) -> None:
    """The generated marker lands within the first 10 lines (hook scan window).

    Both `protect-generated` guards key on the marker, not on a path, so the
    second root inherits the protection only if its marker stays in the window.
    """
    _write_agent(
        tmp_path / "core",
        "code-reviewer",
        _agent_yaml("code-reviewer", extra="tier: low\nclaude:\n  memory: project\n"),
    )
    (agent,) = discover_agents(_roots(tmp_path))
    head = render_agent_md(agent, {}, root).split("\n")[:10]
    assert any(GENERATED_MARKER in line for line in head)


def test_claude_passthrough_reaches_only_the_claude_root(tmp_path: Path) -> None:
    """A Claude-only frontmatter key must not leak into the copilot agent file.

    Copilot's frontmatter schema is a superset in places but `memory` is not in
    it, and an unknown key on a surface we do not control is a liability with no
    upside — the passthrough is declared claude-only, so it renders claude-only.
    """
    _write_agent(
        tmp_path / "core",
        "code-reviewer",
        _agent_yaml("code-reviewer", extra="claude:\n  memory: project\n"),
    )
    (agent,) = discover_agents(_roots(tmp_path))
    assert "memory: project" in render_agent_md(agent, {}, CLAUDE_AGENTS_ROOT)
    assert "memory" not in render_agent_md(agent, {}, COPILOT_AGENTS_ROOT)


def test_claude_passthrough_may_not_shadow_rendered_keys(tmp_path: Path) -> None:
    """A claude map that shadows a rendered frontmatter key is rejected."""
    _write_agent(
        tmp_path / "core",
        "code-reviewer",
        _agent_yaml("code-reviewer", extra="claude:\n  model: opus\n"),
    )
    with pytest.raises(ValidationError, match="may not shadow"):
        discover_agents(_roots(tmp_path))


def _repo_with_agent(tmp_path: Path) -> Path:
    _write_block(tmp_path / ".basicly/core/agents", "honesty", body="Say so if clean.")
    slots = "\n".join(f"  {name}:\n    - text: {name} text" for name in SLOT_ORDER[:-1])
    slots += "\n  constraints:\n    - block: honesty"
    _write_agent(
        tmp_path / ".basicly/core/agents",
        "code-reviewer",
        _agent_yaml("code-reviewer", slots=slots),
    )
    return tmp_path


def _targets(repo: Path, slug: str = "code-reviewer") -> list[Path]:
    """Every root's projected path for *slug*, in AGENTS_OUTPUT_ROOTS order."""
    return [root.target(repo, slug) for root in AGENTS_OUTPUT_ROOTS]


def test_sync_agents_writes_every_root_and_is_idempotent(tmp_path: Path) -> None:
    """sync_agents writes one file per root once, and reports no changes after."""
    repo = _repo_with_agent(tmp_path)
    expected = _targets(repo)
    assert expected == [
        repo / ".claude/agents/code-reviewer.md",
        repo / ".github/agents/code-reviewer.agent.md",
    ]

    first, _pruned = sync_agents(repo)
    assert first.written == expected
    for target in expected:
        assert "Say so if clean." in target.read_text(encoding="utf-8")

    second, _pruned = sync_agents(repo)
    assert second.written == []
    assert second.unchanged == expected


def test_sync_agents_filters_and_prunes_every_root_by_selection(tmp_path: Path) -> None:
    """A tagged agent outside the selection is skipped and pruned from every root."""
    repo = _repo_with_agent(tmp_path)
    source = repo / ".basicly/core/agents/code-reviewer/agent.yaml"
    source.write_text(
        "technologies: [node]\n" + source.read_text(encoding="utf-8"), encoding="utf-8"
    )
    targets = _targets(repo)

    sync_agents(repo)  # no selection recorded: the tagged agent still ships
    assert all(target.is_file() for target in targets)

    selection = frozenset({"python"})
    assert check_synced_agents(repo, selection) == [
        (target, "excluded by technology selection") for target in targets
    ]
    _result, pruned = sync_agents(repo, selection)
    assert pruned == targets and not any(target.exists() for target in targets)
    assert check_synced_agents(repo, selection) == []

    _result, pruned = sync_agents(repo, frozenset({"node"}))
    assert pruned == [] and all(target.is_file() for target in targets)


def test_discover_agents_rejects_unknown_technology(tmp_path: Path) -> None:
    """An out-of-vocabulary tag fails the load (overlay agents skip catalog-lint)."""
    repo = _repo_with_agent(tmp_path)
    source = repo / ".basicly/core/agents/code-reviewer/agent.yaml"
    source.write_text(
        "technologies: [pyton]\n" + source.read_text(encoding="utf-8"), encoding="utf-8"
    )
    with pytest.raises(ValidationError, match="unknown technologies: pyton"):
        discover_agents(default_agent_roots(repo))


def test_check_synced_agents_flags_missing_and_stale(tmp_path: Path) -> None:
    """Check reports missing before build, clean after, stale after a hand-edit."""
    repo = _repo_with_agent(tmp_path)
    targets = _targets(repo)
    assert check_synced_agents(repo) == [(target, "missing") for target in targets]
    sync_agents(repo)
    assert check_synced_agents(repo) == []
    for target in targets:
        target.write_text(target.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    assert check_synced_agents(repo) == [(target, "content mismatch") for target in targets]


@pytest.mark.parametrize("root", AGENTS_OUTPUT_ROOTS, ids=lambda root: root.family)
def test_check_synced_agents_catches_a_hand_edit_in_each_root_alone(
    tmp_path: Path, root: AgentOutputRoot
) -> None:
    """Each root is compared on its own: a gate that cannot fail is not covering it.

    Parametrized rather than asserted on the pair, because a check that only ever
    compared the claude root would still pass the both-roots-edited case above
    (basicly-8sxf).
    """
    repo = _repo_with_agent(tmp_path)
    sync_agents(repo)
    target = root.target(repo, "code-reviewer")

    target.write_text(target.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    assert check_synced_agents(repo) == [(target, "content mismatch")]

    target.unlink()
    assert check_synced_agents(repo) == [(target, "missing")]


def _delete_agent_source(repo: Path, slug: str) -> None:
    """Remove *slug* from the catalog the way a real removal commit does."""
    source = repo / ".basicly/core/agents" / slug / "agent.yaml"
    source.unlink()
    source.parent.rmdir()


def test_sync_agents_prunes_a_projection_whose_source_was_deleted(tmp_path: Path) -> None:
    """A deleted source takes its projection with it, in every root, and only its own.

    The regression is basicly-e2mz.8: both halves iterated catalog sources, so a
    source deleted from the catalog (`code-reviewer`, c3cdb33) left a live agent
    definition on every consumer that nothing would ever look at again.
    """
    repo = _repo_with_agent(tmp_path)
    _write_agent(repo / ".basicly/core/agents", "planner")
    sync_agents(repo)
    orphans = _targets(repo)
    survivors = _targets(repo, "planner")
    assert all(target.is_file() for target in orphans + survivors)

    _delete_agent_source(repo, "code-reviewer")
    _result, pruned = sync_agents(repo)

    assert pruned == orphans
    assert not any(target.exists() for target in orphans)
    assert all(target.is_file() for target in survivors)


def test_check_synced_agents_reports_an_orphan_projection(tmp_path: Path) -> None:
    """The gate half: check fails on a planted orphan instead of reporting up to date.

    Asserted separately from the build because a build that prunes and a check
    that still passes leaves the gate fail-open for anyone who runs `check`
    without `build` — which is what CI does.
    """
    repo = _repo_with_agent(tmp_path)
    sync_agents(repo)
    orphans = _targets(repo)
    assert check_synced_agents(repo) == []

    _delete_agent_source(repo, "code-reviewer")
    assert check_synced_agents(repo) == [(target, ORPHANED_REASON) for target in orphans]

    sync_agents(repo)
    assert check_synced_agents(repo) == []


def test_orphan_pruning_spares_a_hand_written_agent(tmp_path: Path) -> None:
    """A file with no generated marker survives the prune and is not reported.

    The delete predicate is `uninstall`'s: a consumer's own `code-reviewer.md`
    under a projected name is theirs, and deleting it is the one thing here that
    re-running a command cannot undo.
    """
    repo = _repo_with_agent(tmp_path)
    _delete_agent_source(repo, "code-reviewer")
    hand_written = "---\nname: code-reviewer\n---\n\nMy own reviewer.\n"
    for target in _targets(repo):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(hand_written, encoding="utf-8")

    _result, pruned = sync_agents(repo)

    assert pruned == []
    assert check_synced_agents(repo) == []
    for target in _targets(repo):
        assert target.read_text(encoding="utf-8") == hand_written


@pytest.mark.parametrize("root", AGENTS_OUTPUT_ROOTS, ids=lambda root: root.family)
def test_projected_read_only_agent_grants_no_write_tool(
    tmp_path: Path, root: AgentOutputRoot
) -> None:
    """The read-only posture survives the crossing into every root.

    The source lint refuses a read-only agent that *declares* a write tool, but
    the renderer is a second place the grant could widen: a per-family `tools`
    line built from anything but `agent.tools` would defeat the lint silently and
    no drift check would notice, because the projected file would still match its
    own renderer. So assert on the projected frontmatter, resolved through the
    pinned copilot alias table (basicly-8sxf).
    """
    _write_agent(tmp_path / "core", "code-reviewer")
    (agent,) = discover_agents(_roots(tmp_path))

    (line,) = [
        line for line in render_agent_md(agent, {}, root).split("\n") if line.startswith("tools: ")
    ]
    projected = [name.strip() for name in line.removeprefix("tools: ").split(",")]

    assert projected == list(agent.tools)
    assert [name for name in projected if resolve_copilot_tool(name) == "edit"] == []
    folded_writes = {tool.casefold() for tool in WRITE_TOOLS}
    assert [name for name in projected if name.casefold() in folded_writes] == []
