"""Tests for agent source loading, composition, and lint."""

from __future__ import annotations

from pathlib import Path

import pytest

from basicly.agents import (
    GENERATED_MARKER,
    MAX_BODY_CHARS,
    SLOT_ORDER,
    check_synced_agents,
    compose_body,
    compose_description,
    default_agent_roots,
    discover_agents,
    discover_blocks,
    lint_agent_sources,
    render_agent_md,
    sync_agents,
    unknown_block_refs,
)
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
    """A well-formed agent parses with tools, model default, and ordered slots."""
    _write_agent(tmp_path / "core", "code-reviewer")
    agents = discover_agents(_roots(tmp_path))
    assert [agent.slug for agent in agents] == ["code-reviewer"]
    agent = agents[0]
    assert agent.tools == ("Read", "Grep", "Glob")
    assert agent.model == "inherit"
    assert tuple(name for name, _ in agent.slots) == SLOT_ORDER


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


def test_lint_clean_sources_pass(tmp_path: Path) -> None:
    """A coherent agent produces no lint violations."""
    core = tmp_path / ".basicly/core/agents"
    _write_block(core, "honesty")
    _write_agent(core, "code-reviewer")
    assert lint_agent_sources(tmp_path) == []


def test_lint_flags_read_only_posture_with_write_tools(tmp_path: Path) -> None:
    """Read-only posture with a write tool is a violation."""
    core = tmp_path / ".basicly/core/agents"
    _write_agent(core, "code-reviewer", _agent_yaml("code-reviewer", tools="[Read, Edit]"))
    violations = lint_agent_sources(tmp_path)
    assert len(violations) == 1
    assert "read-only but tools grant Edit" in violations[0]


def test_lint_flags_unknown_block_ref(tmp_path: Path) -> None:
    """A dangling block reference is a violation, not a crash."""
    core = tmp_path / ".basicly/core/agents"
    slots = "\n".join(f"  {name}:\n    - text: body" for name in SLOT_ORDER if name != "role")
    slots = "  role:\n    - block: missing\n" + slots
    _write_agent(core, "code-reviewer", _agent_yaml("code-reviewer", slots=slots))
    violations = lint_agent_sources(tmp_path)
    assert len(violations) == 1
    assert "unknown block 'missing'" in violations[0]


def test_lint_flags_oversized_body(tmp_path: Path) -> None:
    """A composed body over the portable cap is a violation."""
    core = tmp_path / ".basicly/core/agents"
    filler = "x" * (MAX_BODY_CHARS + 10)
    slots = "\n".join(f"  {name}:\n    - text: body" for name in SLOT_ORDER if name != "process")
    slots += f"\n  process:\n    - text: {filler}"
    _write_agent(core, "code-reviewer", _agent_yaml("code-reviewer", slots=slots))
    violations = lint_agent_sources(tmp_path)
    assert len(violations) == 1
    assert "portable cap" in violations[0]


def test_lint_reports_load_errors_as_violations(tmp_path: Path) -> None:
    """A source that fails to load lints as one violation instead of raising."""
    core = tmp_path / ".basicly/core/agents"
    _write_agent(core, "code-reviewer", "schema_version: 1\nname: code-reviewer\n")
    violations = lint_agent_sources(tmp_path)
    assert len(violations) == 1
    assert "tools must be a non-empty list" in violations[0]


def test_default_agent_roots_are_core_then_overlay(tmp_path: Path) -> None:
    """Roots load core first so the overlay can override."""
    roots = default_agent_roots(tmp_path)
    assert roots == [
        (tmp_path / ".basicly/core/agents", "core"),
        (tmp_path / ".basicly-local/agents", "user"),
    ]


def test_render_agent_md_shape(tmp_path: Path) -> None:
    """Frontmatter, marker, and body render in the documented shape."""
    _write_agent(tmp_path / "core", "code-reviewer")
    (agent,) = discover_agents(_roots(tmp_path))
    rendered = render_agent_md(agent, {})
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
    assert "model:" not in rendered  # inherit is Claude's default and is omitted
    assert rendered.endswith("The constraints slot.\n")
    assert not rendered.endswith("\n\n")


def test_render_marker_stays_in_protect_generated_window(tmp_path: Path) -> None:
    """The generated marker lands within the first 10 lines (hook scan window)."""
    _write_agent(
        tmp_path / "core",
        "code-reviewer",
        _agent_yaml("code-reviewer", extra="model: haiku\nclaude:\n  memory: project\n"),
    )
    (agent,) = discover_agents(_roots(tmp_path))
    head = render_agent_md(agent, {}).split("\n")[:10]
    assert any(GENERATED_MARKER in line for line in head)
    assert "model: haiku" in head
    assert "memory: project" in head


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


def test_sync_agents_writes_and_is_idempotent(tmp_path: Path) -> None:
    """sync_agents writes the projected file once and reports no changes after."""
    repo = _repo_with_agent(tmp_path)
    first = sync_agents(repo)
    assert first.written == [repo / ".claude/agents/code-reviewer.md"]
    rendered = (repo / ".claude/agents/code-reviewer.md").read_text(encoding="utf-8")
    assert "Say so if clean." in rendered
    second = sync_agents(repo)
    assert second.written == []
    assert second.unchanged == [repo / ".claude/agents/code-reviewer.md"]


def test_check_synced_agents_flags_missing_and_stale(tmp_path: Path) -> None:
    """Check reports missing before build, clean after, stale after a hand-edit."""
    repo = _repo_with_agent(tmp_path)
    target = repo / ".claude/agents/code-reviewer.md"
    assert check_synced_agents(repo) == [(target, "missing")]
    sync_agents(repo)
    assert check_synced_agents(repo) == []
    target.write_text(target.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    assert check_synced_agents(repo) == [(target, "content mismatch")]
