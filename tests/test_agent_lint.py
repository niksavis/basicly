"""Semantic agent lint: what ``catalog lint`` refuses and why.

Split from ``test_agents.py`` by the module-size ratchet (basicly-u2hl.52).
One responsibility and it names itself without an "and": every rule here is a
refusal at authoring time, standing in for a downstream failure that would be
silent — a copilot tool dropped without a word, a preloaded skill that resolves
to nothing, a read-only posture granting a write.
"""

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
    """A coherent agent produces no lint violations."""
    core = tmp_path / ".basicly/core/agents"
    _write_block(core, "honesty")
    _write_agent(core, "code-reviewer")
    assert lint_agent_sources(tmp_path) == []


def test_lint_flags_a_declared_model_and_names_the_tier_field(tmp_path: Path) -> None:
    """A provider model id is unportable, so the refusal must carry the replacement.

    models.dev spells the same model `claude-haiku-4.5` for Copilot and
    `claude-haiku-4-5` for Anthropic, so no `model:` value can be projected for
    every family. The author has to be told the field to use and its values —
    reaching into our schema to learn what to type is not a migration.
    """
    core = tmp_path / ".basicly/core/agents"
    _write_agent(core, "code-reviewer", _agent_yaml("code-reviewer", extra="model: haiku\n"))

    violations = lint_agent_sources(tmp_path)

    assert len(violations) == 1, violations
    assert violations[0].startswith(".basicly/core/agents/code-reviewer/agent.yaml: ")
    assert "model: haiku" in violations[0]
    # Spelled out, not joined from MODEL_TIERS: an assertion derived from the same
    # constant as the message would survive the vocabulary changing under it.
    assert "tier: low | medium | high | maximum" in violations[0]


def test_lint_flags_a_declared_model_in_the_overlay(tmp_path: Path) -> None:
    """The overlay is why this rule lives here: schema validation globs core only."""
    overlay = tmp_path / ".basicly-local/agents"
    _write_agent(overlay, "code-reviewer", _agent_yaml("code-reviewer", extra="model: sonnet\n"))

    violations = lint_agent_sources(tmp_path)

    assert len(violations) == 1, violations
    assert violations[0].startswith(".basicly-local/agents/code-reviewer/agent.yaml: ")
    assert "tier" in violations[0]


def test_lint_reports_a_declared_model_alongside_the_other_defects(tmp_path: Path) -> None:
    """The rule is not a parse-time raise, so one bad source still yields every finding."""
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
    """The replacement field is not itself a violation."""
    core = tmp_path / ".basicly/core/agents"
    _write_agent(core, "code-reviewer", _agent_yaml("code-reviewer", extra="tier: low\n"))
    assert lint_agent_sources(tmp_path) == []


def test_lint_flags_read_only_posture_with_write_tools(tmp_path: Path) -> None:
    """Read-only posture with a write tool is a violation."""
    core = tmp_path / ".basicly/core/agents"
    _write_agent(core, "code-reviewer", _agent_yaml("code-reviewer", tools="[Read, Edit]"))
    violations = lint_agent_sources(tmp_path)
    assert len(violations) == 1
    assert "read-only but tools grant Edit" in violations[0]


@pytest.mark.parametrize("tool", ["edit", "WRITE", "MULTIEDIT", "notebookedit", "create"])
def test_lint_flags_read_only_posture_with_a_write_tool_in_any_casing(
    tmp_path: Path, tool: str
) -> None:
    """Casing is not a loophole: copilot resolves its tool aliases case insensitively.

    A lowercase `edit` grants the same writes `Edit` does, so an exact-match
    check would pass a read-only agent that really can write (basicly-e9jc).
    `create` is copilot's file-creating primary and has no claude spelling at all.
    """
    core = tmp_path / ".basicly/core/agents"
    _write_agent(core, "code-reviewer", _agent_yaml("code-reviewer", tools=f"[Read, {tool}]"))

    violations = lint_agent_sources(tmp_path)

    # Both halves the author needs: the offending tool as authored, and the
    # posture claim it contradicts. `create` additionally trips the alias
    # resolution rule (it is absent from GitHub's published table), so the count
    # is not asserted here.
    assert any(f"read-only but tools grant {tool}" in v for v in violations), violations


def test_lint_accepts_read_tools_in_any_casing(tmp_path: Path) -> None:
    """Folding the comparison must not turn a read tool into a violation."""
    core = tmp_path / ".basicly/core/agents"
    _write_agent(core, "code-reviewer", _agent_yaml("code-reviewer", tools="[read, GREP, Glob]"))
    assert lint_agent_sources(tmp_path) == []


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


def test_every_copilot_edit_alias_fails_the_read_only_posture_check(tmp_path: Path) -> None:
    """The pinned table drives the posture check, so a new write alias cannot slip in.

    Guards the derivation in `_WRITE_TOOLS_FOLDED`: if GitHub adds an alias to the
    `edit` primary and someone updates COPILOT_TOOL_ALIASES, the read-only check
    has to widen with it rather than leave a hole.
    """
    core = tmp_path / ".basicly/core/agents"
    for tool in sorted({"edit", *COPILOT_TOOL_ALIASES["edit"], *WRITE_TOOLS}):
        _write_agent(core, "code-reviewer", _agent_yaml("code-reviewer", tools=f"[Read, {tool}]"))
        # Membership, not equality: `Create` is the copilot CLI's internal write
        # primary and is deliberately absent from GitHub's published alias table,
        # so it also trips the resolution rule below.
        assert (
            ".basicly/core/agents/code-reviewer/agent.yaml: posture declares read-only "
            f"but tools grant {tool}"
        ) in lint_agent_sources(tmp_path), tool


def test_lint_flags_a_tool_that_resolves_to_nothing_on_copilot(tmp_path: Path) -> None:
    """A name copilot would silently drop is an authoring defect, reported loudly here.

    Claude Code refuses to launch and names an unresolved entry; copilot drops it
    with no error, so without this rule a typo would ship as a quietly useless
    agent on that root (basicly-8sxf).
    """
    core = tmp_path / ".basicly/core/agents"
    _write_agent(core, "code-reviewer", _agent_yaml("code-reviewer", tools="[Read, Raed]"))

    violations = lint_agent_sources(tmp_path)

    assert len(violations) == 1, violations
    assert "tool(s) Raed resolve to nothing" in violations[0]
    assert ".github/agents" in violations[0]
    # The remedy has to name an accepted spelling, not just the refusal.
    assert "Grep" in violations[0]


def _write_probe(tmp_path: Path, declared: str) -> None:
    """An agent whose Claude passthrough preloads *declared*, beside one real skill."""
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
    """The host preloads silently, so an unresolved name is an agent without its method.

    Same failure shape as an unrecognised copilot tool (basicly-8sxf): nothing
    downstream reports it, so the loud failure has to be restored at authoring time.
    """
    _write_probe(tmp_path, "[nope]")

    violations = lint_agent_sources(tmp_path)

    assert any("claude.skills names 'nope'" in v for v in violations), violations


def test_a_preloaded_skill_that_exists_passes(tmp_path: Path) -> None:
    """The positive control: the check above must not fire on a real name."""
    _write_probe(tmp_path, "[real]")

    violations = lint_agent_sources(tmp_path)

    assert not any("claude.skills" in v for v in violations), violations


def test_a_preloaded_skill_may_be_a_bare_string(tmp_path: Path) -> None:
    """The passthrough is untyped because its shape is the vendor's to define.

    A checker that understood only the list form would report a false unknown on a
    source the host reads fine.
    """
    _write_probe(tmp_path, "real")

    assert not any("claude.skills" in v for v in lint_agent_sources(tmp_path))
    assert unknown_skill_refs(discover_agents(default_agent_roots(tmp_path))[0], {"real"}) == []


# --- an artifact contract names its schema's field set -------------------------
#
# The refusal these stand in for is not an authoring-time one: SHIP has already
# merged when a release record is refused, so a curator reply carrying a key the
# schema forbids costs the record and blocks nothing (basicly-s07cgc). The
# producer's contract is the only place the field set can be stated in time.

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMAS_DIR = REPO_ROOT / ".basicly/core/schemas"
FIELD_SET_BLOCK = "artifact-field-set"

# Which wired kind is composed from an **agent's reply**, against the agent that
# writes it. Declared, never derived: only this population can be refused for a
# key a model chose, and the other one — a payload the engine derives from its own
# facts — would be actively wrong to brief a model about. A kind's absence from an
# agent source is ambiguous between the two, which is what the test below pins.
REPLY_COMPOSED: dict[str, str] = {handoff.RELEASE_RECORD: "curator"}
ENGINE_DERIVED = frozenset({handoff.IMPLEMENTATION_PLAN, handoff.CHANGE_SUMMARY})


def _required_field_names(node: object) -> set[str]:
    """Every property name *node* requires, at any depth (pure).

    Recursive because the field set a producer has to be told is not the top-level
    one: `suggested_wording` was refused two levels down, on `unsupported[]`.
    """
    if not isinstance(node, dict):
        return set()
    names = {name for name in node.get("required", []) if isinstance(name, str)}
    properties = node.get("properties")
    if isinstance(properties, dict):
        for child in properties.values():
            names |= _required_field_names(child)
    return names | _required_field_names(node.get("items"))


def _unnamed_required_fields(contract: str, schema: object) -> list[str]:
    """The required field names *contract* does not name as fields (pure).

    Backticked, not merely present: "claim" occurs in this role's prose throughout
    without ever telling the model a key by that name exists, so a bare substring
    test would pass on the contract that caused the defect.
    """
    return sorted(f for f in _required_field_names(schema) if f"`{f}`" not in contract)


def _contract_text(slug: str) -> tuple[str, tuple[str, ...]]:
    """The named agent's rendered output contract, and the blocks it references."""
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
    """A producer told to emit an artifact and not told its field set invents one."""
    schema = json.loads((SCHEMAS_DIR / f"{kind}.schema.json").read_text(encoding="utf-8"))
    contract, _ = _contract_text(slug)

    assert _unnamed_required_fields(contract, schema) == []


@pytest.mark.parametrize(("kind", "slug"), sorted(REPLY_COMPOSED.items()))
def test_a_reply_composed_artifact_contract_closes_the_object(kind: str, slug: str) -> None:
    """Naming the fields is half of it; the other half is that no other key is admitted.

    Asserted on the block reference rather than on wording, so the rule stays one
    text with one owner as the population grows past its first member.
    """
    _, blocks = _contract_text(slug)

    assert FIELD_SET_BLOCK in blocks, f"{slug} emits {kind} without the closed-object rule"


def test_the_field_set_check_fails_a_contract_that_names_no_fields() -> None:
    """The positive control: a passing gate above must be able to fail.

    This is the curator's contract as it was when a release record was refused —
    every claim named in prose, not one key named as a key.
    """
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
    """A new wired kind is a decision, not a default.

    Without this a kind added to `PRODUCERS` would join neither population and be
    briefed by nobody — the same silence that cost the release records, one
    artifact further out.
    """
    wired = {kind for kind in handoff.PRODUCERS if handoff.wired(kind)}

    assert wired == set(REPLY_COMPOSED) | ENGINE_DERIVED
