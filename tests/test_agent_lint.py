from __future__ import annotations

import json
from pathlib import Path

import pytest

from basicly import handoff
from basicly.agents import (
    MAX_BODY_CHARS,
    SLOT_ORDER,
    default_agent_roots,
    discover_agents,
    discover_blocks,
    lint_agent_sources,
    unknown_skill_refs,
)
from basicly.copilot_tools import COPILOT_TOOL_ALIASES, WRITE_TOOLS
from tests.agent_helpers import _agent_yaml, _write_agent, _write_block


def test_lint_clean_sources_pass(tmp_path: Path) -> None:
    core = tmp_path / ".basicly/core/agents"
    _write_block(core, "honesty")
    _write_agent(core, "code-reviewer")
    assert lint_agent_sources(tmp_path) == []


def test_lint_flags_a_declared_model_and_names_the_tier_field(tmp_path: Path) -> None:

    core = tmp_path / ".basicly/core/agents"
    _write_agent(core, "code-reviewer", _agent_yaml("code-reviewer", extra="model: haiku\n"))

    violations = lint_agent_sources(tmp_path)

    assert len(violations) == 1, violations
    assert violations[0].startswith(".basicly/core/agents/code-reviewer/agent.yaml: ")
    assert "model: haiku" in violations[0]
    assert "tier: low | medium | high | maximum" in violations[0]


def test_lint_flags_a_declared_model_in_the_overlay(tmp_path: Path) -> None:
    overlay = tmp_path / ".basicly-local/agents"
    _write_agent(overlay, "code-reviewer", _agent_yaml("code-reviewer", extra="model: sonnet\n"))

    violations = lint_agent_sources(tmp_path)

    assert len(violations) == 1, violations
    assert violations[0].startswith(".basicly-local/agents/code-reviewer/agent.yaml: ")
    assert "tier" in violations[0]


def test_lint_reports_a_declared_model_alongside_the_other_defects(tmp_path: Path) -> None:
    core = tmp_path / ".basicly/core/agents"
    _write_agent(
        core,
        "code-reviewer",
        _agent_yaml("code-reviewer", tools="[Read, Edit]", extra="model: haiku\n"),
    )

    violations = lint_agent_sources(tmp_path)

    assert len(violations) == 2, violations
    assert any("tier" in v for v in violations)
    assert any("read-only but tools grant Edit" in v for v in violations)


def test_lint_accepts_a_declared_model_tier(tmp_path: Path) -> None:
    core = tmp_path / ".basicly/core/agents"
    _write_agent(core, "code-reviewer", _agent_yaml("code-reviewer", extra="tier: low\n"))
    assert lint_agent_sources(tmp_path) == []


def test_lint_flags_read_only_posture_with_write_tools(tmp_path: Path) -> None:
    core = tmp_path / ".basicly/core/agents"
    _write_agent(core, "code-reviewer", _agent_yaml("code-reviewer", tools="[Read, Edit]"))
    violations = lint_agent_sources(tmp_path)
    assert len(violations) == 1
    assert "read-only but tools grant Edit" in violations[0]


@pytest.mark.parametrize("tool", ["edit", "WRITE", "MULTIEDIT", "notebookedit", "create"])
def test_lint_flags_read_only_posture_with_a_write_tool_in_any_casing(
    tmp_path: Path, tool: str
) -> None:

    core = tmp_path / ".basicly/core/agents"
    _write_agent(core, "code-reviewer", _agent_yaml("code-reviewer", tools=f"[Read, {tool}]"))

    violations = lint_agent_sources(tmp_path)

    assert any(f"read-only but tools grant {tool}" in v for v in violations), violations


def test_lint_accepts_read_tools_in_any_casing(tmp_path: Path) -> None:
    core = tmp_path / ".basicly/core/agents"
    _write_agent(core, "code-reviewer", _agent_yaml("code-reviewer", tools="[read, GREP, Glob]"))
    assert lint_agent_sources(tmp_path) == []


def test_lint_flags_unknown_block_ref(tmp_path: Path) -> None:
    core = tmp_path / ".basicly/core/agents"
    slots = "\n".join(f"  {name}:\n    - text: body" for name in SLOT_ORDER if name != "role")
    slots = "  role:\n    - block: missing\n" + slots
    _write_agent(core, "code-reviewer", _agent_yaml("code-reviewer", slots=slots))
    violations = lint_agent_sources(tmp_path)
    assert len(violations) == 1
    assert "unknown block 'missing'" in violations[0]


def test_lint_flags_oversized_body(tmp_path: Path) -> None:
    core = tmp_path / ".basicly/core/agents"
    filler = "x" * (MAX_BODY_CHARS + 10)
    slots = "\n".join(f"  {name}:\n    - text: body" for name in SLOT_ORDER if name != "process")
    slots += f"\n  process:\n    - text: {filler}"
    _write_agent(core, "code-reviewer", _agent_yaml("code-reviewer", slots=slots))
    violations = lint_agent_sources(tmp_path)
    assert len(violations) == 1
    assert "portable cap" in violations[0]


def test_lint_reports_load_errors_as_violations(tmp_path: Path) -> None:
    core = tmp_path / ".basicly/core/agents"
    _write_agent(core, "code-reviewer", "schema_version: 1\nname: code-reviewer\n")
    violations = lint_agent_sources(tmp_path)
    assert len(violations) == 1
    assert "tools must be a non-empty list" in violations[0]


def test_every_copilot_edit_alias_fails_the_read_only_posture_check(tmp_path: Path) -> None:

    core = tmp_path / ".basicly/core/agents"
    for tool in sorted({"edit", *COPILOT_TOOL_ALIASES["edit"], *WRITE_TOOLS}):
        _write_agent(core, "code-reviewer", _agent_yaml("code-reviewer", tools=f"[Read, {tool}]"))
        assert (
            ".basicly/core/agents/code-reviewer/agent.yaml: posture declares read-only "
            f"but tools grant {tool}"
        ) in lint_agent_sources(tmp_path), tool


def test_lint_flags_a_tool_that_resolves_to_nothing_on_copilot(tmp_path: Path) -> None:

    core = tmp_path / ".basicly/core/agents"
    _write_agent(core, "code-reviewer", _agent_yaml("code-reviewer", tools="[Read, Raed]"))

    violations = lint_agent_sources(tmp_path)

    assert len(violations) == 1, violations
    assert "tool(s) Raed resolve to nothing" in violations[0]
    assert ".github/agents" in violations[0]
    assert "Grep" in violations[0]


def _write_probe(tmp_path: Path, declared: str) -> None:
    _write_agent(
        tmp_path / ".basicly" / "core" / "agents",
        "probe",
        _agent_yaml("probe", extra=f"claude:\n  skills: {declared}\n"),
    )
    skill = tmp_path / ".basicly" / "core" / "skills" / "real" / "skill.yaml"
    skill.parent.mkdir(parents=True, exist_ok=True)
    skill.write_text(
        "schema_version: 1\nname: real\ninvocation: model\ndescription: d\n"
        "instructions: |\n  # x\n",
        encoding="utf-8",
    )


def test_a_preloaded_skill_that_does_not_exist_is_refused(tmp_path: Path) -> None:

    _write_probe(tmp_path, "[nope]")

    violations = lint_agent_sources(tmp_path)

    assert any("claude.skills names 'nope'" in v for v in violations), violations


def test_a_preloaded_skill_that_exists_passes(tmp_path: Path) -> None:
    _write_probe(tmp_path, "[real]")

    violations = lint_agent_sources(tmp_path)

    assert not any("claude.skills" in v for v in violations), violations


def test_a_preloaded_skill_may_be_a_bare_string(tmp_path: Path) -> None:

    _write_probe(tmp_path, "real")

    assert not any("claude.skills" in v for v in lint_agent_sources(tmp_path))
    assert unknown_skill_refs(discover_agents(default_agent_roots(tmp_path))[0], {"real"}) == []


REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMAS_DIR = REPO_ROOT / ".basicly/core/schemas"
FIELD_SET_BLOCK = "artifact-field-set"

REPLY_COMPOSED: dict[str, str] = {handoff.RELEASE_RECORD: "curator"}
ENGINE_DERIVED = frozenset({handoff.IMPLEMENTATION_PLAN, handoff.CHANGE_SUMMARY})


def _required_field_names(node: object) -> set[str]:

    if not isinstance(node, dict):
        return set()
    names = {name for name in node.get("required", []) if isinstance(name, str)}
    properties = node.get("properties")
    if isinstance(properties, dict):
        for child in properties.values():
            names |= _required_field_names(child)
    return names | _required_field_names(node.get("items"))


def _unnamed_required_fields(contract: str, schema: object) -> list[str]:

    return sorted(f for f in _required_field_names(schema) if f"`{f}`" not in contract)


def _contract_text(slug: str) -> tuple[str, tuple[str, ...]]:
    roots = default_agent_roots(REPO_ROOT)
    agent = next(a for a in discover_agents(roots) if a.slug == slug)
    blocks = discover_blocks(roots)
    items = agent.slot("output_contract")
    rendered = "\n\n".join(
        item.value if item.kind == "text" else blocks[item.value].body for item in items
    )
    return rendered, tuple(i.value for i in items if i.kind == "block")


@pytest.mark.parametrize(("kind", "slug"), sorted(REPLY_COMPOSED.items()))
def test_a_reply_composed_artifact_contract_names_every_required_field(
    kind: str, slug: str
) -> None:
    schema = json.loads((SCHEMAS_DIR / f"{kind}.schema.json").read_text(encoding="utf-8"))
    contract, _ = _contract_text(slug)

    assert _unnamed_required_fields(contract, schema) == []


@pytest.mark.parametrize(("kind", "slug"), sorted(REPLY_COMPOSED.items()))
def test_a_reply_composed_artifact_contract_closes_the_object(kind: str, slug: str) -> None:

    _, blocks = _contract_text(slug)

    assert FIELD_SET_BLOCK in blocks, f"{slug} emits {kind} without the closed-object rule"


def test_the_field_set_check_fails_a_contract_that_names_no_fields() -> None:

    schema = json.loads(
        (SCHEMAS_DIR / f"{handoff.RELEASE_RECORD}.schema.json").read_text(encoding="utf-8")
    )
    before = (
        "Emit a `release-record`: each claim with its evidence, each unsupported "
        "claim named, and the post-ship action pre-declared before the tag moves."
    )

    assert _unnamed_required_fields(before, schema) == [
        "claim",
        "claims",
        "evidence",
        "issue",
        "kind",
        "post_ship_action",
        "reference",
        "schema_version",
        "unsupported",
        "why",
    ]


def test_every_wired_artifact_kind_declares_who_composes_it() -> None:

    wired = {kind for kind in handoff.PRODUCERS if handoff.wired(kind)}

    assert wired == set(REPLY_COMPOSED) | ENGINE_DERIVED
