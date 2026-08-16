"""Tests for the catalog-lint gate."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

from basicly import loop_state
from basicly.catalog_lint import lint_catalog, listing_budget_warnings, skill_warnings
from basicly.schema import MODEL_TIERS
from basicly.skill_source import discover_skills

REPO = Path(__file__).parent.parent
VALID_SKILL = (
    "schema_version: 1\nname: s\ninvocation: model\ndescription: d\ninstructions: |\n  body\n"
)
VALID_FRAGMENT = (
    "schema_version: 1\nid: f\ndescription: d\ncategory: project\n"
    "applies_to: [all]\nbody: |\n  - x\n"
)


def _catalog(tmp_path: Path) -> Path:
    """Build a minimal catalog with real schemas and one valid skill + fragment."""
    schemas = tmp_path / ".basicly/core/schemas"
    schemas.mkdir(parents=True)
    for name in ("skill.schema.json", "fragment.schema.json", "agent.schema.json"):
        (schemas / name).write_text(
            (REPO / ".basicly/core/schemas" / name).read_text(encoding="utf-8"), encoding="utf-8"
        )
    skill = tmp_path / ".basicly/core/skills/s/skill.yaml"
    skill.parent.mkdir(parents=True)
    skill.write_text(VALID_SKILL, encoding="utf-8")
    frag = tmp_path / ".basicly/core/fragments/project/f.fragment.yaml"
    frag.parent.mkdir(parents=True)
    frag.write_text(VALID_FRAGMENT, encoding="utf-8")
    return tmp_path


def test_clean_catalog_passes(tmp_path: Path) -> None:
    """A well-formed catalog reports no violations."""
    assert lint_catalog(_catalog(tmp_path)) == []


def test_flags_skill_md_source(tmp_path: Path) -> None:
    """A SKILL.md left in a source dir is a violation."""
    root = _catalog(tmp_path)
    (root / ".basicly/core/skills/legacy").mkdir()
    (root / ".basicly/core/skills/legacy/SKILL.md").write_text("x\n", encoding="utf-8")
    assert any("SKILL.md" in v for v in lint_catalog(root))


def test_flags_fragment_md_source(tmp_path: Path) -> None:
    """A *.fragment.md source is a violation."""
    root = _catalog(tmp_path)
    (root / ".basicly/core/fragments/project/legacy.fragment.md").write_text(
        "x\n", encoding="utf-8"
    )
    assert any("fragment.md" in v for v in lint_catalog(root))


def test_flags_yml_extension(tmp_path: Path) -> None:
    """A .yml file anywhere under the catalog is a violation."""
    root = _catalog(tmp_path)
    (root / ".basicly/core/stray.yml").write_text("a: 1\n", encoding="utf-8")
    assert any(".yml" in v for v in lint_catalog(root))


def test_flags_schema_violation(tmp_path: Path) -> None:
    """A source missing a required field fails schema validation."""
    root = _catalog(tmp_path)
    # drop the required 'instructions' field
    (root / ".basicly/core/skills/s/skill.yaml").write_text(
        "schema_version: 1\nname: s\ninvocation: model\ndescription: d\n", encoding="utf-8"
    )
    violations = lint_catalog(root)
    assert any("skill.yaml" in v for v in violations)


def test_enforced_by_cited_in_body_passes(tmp_path: Path) -> None:
    """A fragment that cites its enforced_by command in the body is clean."""
    root = _catalog(tmp_path)
    (root / ".basicly/core/fragments/project/f.fragment.yaml").write_text(
        "schema_version: 1\nid: f\ndescription: d\ncategory: code-style\n"
        "applies_to: [all]\nenforced_by: [ruff format]\n"
        "body: |\n  Formatting is enforced by `ruff format`.\n",
        encoding="utf-8",
    )
    assert lint_catalog(root) == []


def test_enforced_by_not_cited_is_flagged(tmp_path: Path) -> None:
    """A fragment declaring enforced_by without citing it in the body is a violation."""
    root = _catalog(tmp_path)
    (root / ".basicly/core/fragments/project/f.fragment.yaml").write_text(
        "schema_version: 1\nid: f\ndescription: d\ncategory: code-style\n"
        "applies_to: [all]\nenforced_by: [ruff format]\n"
        "body: |\n  Always indent with four spaces.\n",
        encoding="utf-8",
    )
    violations = lint_catalog(root)
    assert any("enforced_by command 'ruff format' is not cited" in v for v in violations)


def test_no_enforced_by_is_a_noop(tmp_path: Path) -> None:
    """A fragment without enforced_by triggers no enforcement-pointer violation."""
    root = _catalog(tmp_path)
    assert not any("enforced_by" in v for v in lint_catalog(root))


def test_valid_technologies_pass(tmp_path: Path) -> None:
    """Vocabulary-conformant technologies on a skill and a fragment are clean."""
    root = _catalog(tmp_path)
    (root / ".basicly/core/skills/s/skill.yaml").write_text(
        "schema_version: 1\nname: s\ninvocation: model\ndescription: d\ntechnologies: [python]\n"
        "instructions: |\n  body\n",
        encoding="utf-8",
    )
    (root / ".basicly/core/fragments/project/f.fragment.yaml").write_text(
        VALID_FRAGMENT.replace("applies_to: [all]\n", "applies_to: [all]\ntechnologies: [zsh]\n"),
        encoding="utf-8",
    )
    assert lint_catalog(root) == []


def test_flags_unknown_technology(tmp_path: Path) -> None:
    """A technologies value outside the controlled vocabulary is a violation."""
    root = _catalog(tmp_path)
    (root / ".basicly/core/skills/s/skill.yaml").write_text(
        "schema_version: 1\nname: s\ninvocation: model\ndescription: d\ntechnologies: [cobol]\n"
        "instructions: |\n  body\n",
        encoding="utf-8",
    )
    violations = lint_catalog(root)
    assert any("unknown technologies: cobol" in v for v in violations)


def test_flags_unknown_technology_in_hooks_manifest(tmp_path: Path) -> None:
    """The hooks manifest participates in the vocabulary check (it has no schema)."""
    root = _catalog(tmp_path)
    hooks = root / ".basicly/core/hooks"
    hooks.mkdir(parents=True)
    (hooks / "hooks.yaml").write_text(
        "hooks:\n  - id: x\n    script: x.py\n    stage: pre-commit\n    technologies: [fortran]\n",
        encoding="utf-8",
    )
    violations = lint_catalog(root)
    assert any("unknown technologies: fortran" in v for v in violations)


def test_flags_skill_name_directory_mismatch(tmp_path: Path) -> None:
    """A skill whose name field differs from its directory is a violation (spec: name==dir)."""
    root = _catalog(tmp_path)
    skill = root / ".basicly/core/skills/mismatch/skill.yaml"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "schema_version: 1\nname: other\n"
        "invocation: model\ndescription: d\ninstructions: |\n  body\n",
        encoding="utf-8",
    )
    assert any("must match its directory" in v for v in lint_catalog(root))


def test_flags_invalid_skill_name(tmp_path: Path) -> None:
    """A name with uppercase/consecutive hyphens violates the Agent Skills naming rule."""
    root = _catalog(tmp_path)
    skill = root / ".basicly/core/skills/bad--name/skill.yaml"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "schema_version: 1\nname: bad--name\n"
        "invocation: model\ndescription: d\ninstructions: |\n  body\n",
        encoding="utf-8",
    )
    assert any("no leading, trailing, or consecutive hyphen" in v for v in lint_catalog(root))


def test_skill_body_over_limit_warns_but_does_not_fail(tmp_path: Path) -> None:
    """An oversized SKILL.md body is a warning (advisory), not a hard lint violation."""
    root = _catalog(tmp_path)
    body = "\n".join(f"  line {n}" for n in range(600))
    skill = root / ".basicly/core/skills/big/skill.yaml"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        f"schema_version: 1\nname: big\n"
        f"invocation: model\ndescription: d\ninstructions: |\n{body}\n",
        encoding="utf-8",
    )
    assert lint_catalog(root) == []  # not a hard failure
    assert any("keep it under" in w for w in skill_warnings(root))


def test_deep_file_reference_warns(tmp_path: Path) -> None:
    """A file reference more than one level deep is surfaced as a warning."""
    root = _catalog(tmp_path)
    skill = root / ".basicly/core/skills/refs/skill.yaml"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "schema_version: 1\nname: refs\ninvocation: model\ndescription: d\ninstructions: |\n"
        "  See references/sub/deep.md for details.\n",
        encoding="utf-8",
    )
    assert any("more than one level deep" in w for w in skill_warnings(root))


def test_one_level_markdown_link_does_not_warn(tmp_path: Path) -> None:
    """A normal one-level markdown link must not be misread as a two-level path."""
    root = _catalog(tmp_path)
    skill = root / ".basicly/core/skills/refs/skill.yaml"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "schema_version: 1\nname: refs\ninvocation: model\ndescription: d\ninstructions: |\n"
        "  See [the guide](references/guide.md) and run scripts/fix.sh.\n",
        encoding="utf-8",
    )
    assert not any("more than one level deep" in w for w in skill_warnings(root))


# --- Invocation axis (basicly-m4zv.1) -----------------------------------------


def _skill_source(root: Path, slug: str, body: str) -> Path:
    path = root / ".basicly/core/skills" / slug / "skill.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


_INSTRUCTIONS = "instructions: |\n  # x\n\n  text\n"


def test_a_missing_invocation_declaration_fails_the_lint(tmp_path: Path) -> None:
    """Until an entry knows whether anything can route to it, 2b is not well-posed."""
    root = _catalog(tmp_path)
    _skill_source(root, "undeclared", f"schema_version: 1\nname: undeclared\n{_INSTRUCTIONS}")

    violations = lint_catalog(root)

    assert any("no 'invocation' declared" in v for v in violations), violations


def test_a_missing_invocation_names_both_values_and_the_safe_migration(tmp_path: Path) -> None:
    """The field is required with no default, so the refusal must carry the fix.

    A consumer catalog authored before the axis existed fails on upgrade, and the
    owner ruled the break stays rather than defaulting (basicly-m4zv.9). Reaching
    into our source to learn what to type is not an acceptable migration, so both
    valid values and the one that preserves existing behaviour are in the text —
    and the raw jsonschema line is gone, so one defect yields one diagnostic.
    """
    root = _catalog(tmp_path)
    _skill_source(
        root,
        "legacy",
        f"schema_version: 1\nname: legacy\ndescription: d\n{_INSTRUCTIONS}",
    )

    violations = [v for v in lint_catalog(root) if "legacy" in v]

    assert len(violations) == 1, f"one defect must yield one diagnostic: {violations}"
    assert "invocation: model" in violations[0]
    assert "invocation: user" in violations[0]
    assert "is a required property" not in violations[0]


def test_an_unknown_invocation_value_fails_the_lint(tmp_path: Path) -> None:
    """The axis has exactly two positions; a third would be unenforceable."""
    root = _catalog(tmp_path)
    _skill_source(
        root,
        "bogus",
        f"schema_version: 1\nname: bogus\ninvocation: sometimes\ndescription: d\n{_INSTRUCTIONS}",
    )

    assert any("sometimes" in v for v in lint_catalog(root))


def test_a_user_invoked_entry_carrying_a_description_fails_the_lint(tmp_path: Path) -> None:
    """This is the waste the axis exists to find: context load bought for no reach."""
    root = _catalog(tmp_path)
    _skill_source(
        root,
        "handrun",
        f"schema_version: 1\nname: handrun\ninvocation: user\ndescription: d\n{_INSTRUCTIONS}",
    )

    violations = [v for v in lint_catalog(root) if "handrun" in v]

    assert any("must not carry a description" in v for v in violations), violations


def test_a_model_invoked_entry_without_a_description_fails_the_lint(tmp_path: Path) -> None:
    """A model-invoked entry with no description cannot be routed to, yet still costs a name."""
    root = _catalog(tmp_path)
    _skill_source(
        root, "silent", f"schema_version: 1\nname: silent\ninvocation: model\n{_INSTRUCTIONS}"
    )

    violations = [v for v in lint_catalog(root) if "silent" in v]

    assert any("needs a description" in v for v in violations), violations


def test_every_shipped_skill_declares_the_axis() -> None:
    """The core catalog is the first consumer of its own rule."""
    for path in sorted((REPO / ".basicly/core/skills").glob("*/skill.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert data.get("invocation") in {"model", "user"}, f"{path.parent.name} has no axis"


# --- Model tier, not a provider model id (basicly-kjc5.58) --------------------

_AGENT_SLOTS = "".join(
    f"  {name}:\n    - text: the {name} slot\n"
    for name in ("role", "startup", "process", "output_contract", "constraints")
)


def _agent_source(
    root: Path, slug: str, extra: str, agents_dir: str = ".basicly/core/agents"
) -> Path:
    path = root / agents_dir / slug / "agent.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"schema_version: 1\nname: {slug}\npurpose: Reviews things.\n"
        f"triggers: Use proactively after changes.\nreturns: Returns findings.\n"
        f"posture: Read-only.\ntools: [Read, Grep, Glob]\n{extra}slots:\n{_AGENT_SLOTS}",
        encoding="utf-8",
    )
    return path


def test_a_declared_model_tier_passes_the_catalog_lint(tmp_path: Path) -> None:
    """The portable field is the accepted way to say how capable an agent must be."""
    root = _catalog(tmp_path)
    _agent_source(root, "reviewer", "tier: high\n")

    assert lint_catalog(root) == []


def test_an_agent_pinning_a_model_names_the_source_and_the_tier_field(tmp_path: Path) -> None:
    """`model:` survives as a schema property only so this message can replace it.

    The schema sets additionalProperties: false, so dropping the property would
    fail the source with "Additional properties are not allowed ('model' was
    unexpected)" — which names neither the replacement field nor its values. The
    property stays known and the agent lint owns the actionable diagnostic.
    """
    root = _catalog(tmp_path)
    _agent_source(root, "reviewer", "model: haiku\n")

    violations = [v for v in lint_catalog(root) if "reviewer" in v]

    assert len(violations) == 1, f"one defect must yield one diagnostic: {violations}"
    assert ".basicly/core/agents/reviewer/agent.yaml" in violations[0]
    assert "tier: low | medium | high | maximum" in violations[0]
    assert "not allowed" not in violations[0]


def test_no_shipped_agent_source_declares_a_model() -> None:
    """The core catalog is the first consumer of its own rule."""
    for path in sorted((REPO / ".basicly/core/agents").glob("*/agent.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert "model" not in data, f"{path.parent.name} pins a provider model id"


def test_every_shipped_agent_source_declares_a_model_tier() -> None:
    """D26: a dispatch with no resolved tier is a bug, not a default.

    `code-reviewer` and `security-auditor`, now `auditor`, shipped with no tier until
    basicly-plhx, and `basicly install` vendors both to consumers, so the
    omission travelled. An omitted tier does not fall back to a cheap model - it
    inherits the session's, usually the most expensive one.
    """
    for path in sorted((REPO / ".basicly/core/agents").glob("*/agent.yaml")):
        tier = yaml.safe_load(path.read_text(encoding="utf-8")).get("tier")
        assert tier in MODEL_TIERS, f"{path.parent.name} declares no usable model tier: {tier!r}"


def test_the_agent_schema_tier_enum_matches_the_model_tier_vocabulary() -> None:
    """The JSON Schema restates MODEL_TIERS, so a tripwire keeps the two in step."""
    schema = json.loads(
        (REPO / ".basicly/core/schemas/agent.schema.json").read_text(encoding="utf-8")
    )
    assert schema["properties"]["tier"]["enum"] == list(MODEL_TIERS)


def test_the_agent_schema_requires_a_tier() -> None:
    """The schema is the half of the rule an editor and `install` both read."""
    schema = json.loads(
        (REPO / ".basicly/core/schemas/agent.schema.json").read_text(encoding="utf-8")
    )
    assert "tier" in schema["required"]


@pytest.mark.parametrize("agents_dir", [".basicly/core/agents", ".basicly-local/agents"])
def test_an_agent_source_declaring_no_tier_fails_the_catalog_lint(
    tmp_path: Path, agents_dir: str
) -> None:
    """Both roots refuse it, and both say the same thing.

    The schema's own "'tier' is a required property" line is suppressed
    (`_TIER_OWNED_REQUIRED`) because it names neither the vocabulary nor the
    reason, and it never reached the overlay root at all - which is the same
    asymmetry basicly-axqe closed for the vocabulary check.
    """
    root = _catalog(tmp_path)
    _agent_source(root, "reviewer", "", agents_dir)

    violations = [v for v in lint_catalog(root) if "reviewer" in v]

    assert len(violations) == 1, f"one defect must yield one diagnostic: {violations}"
    assert f"{agents_dir}/reviewer/agent.yaml" in violations[0]
    # Spelled out rather than joined from MODEL_TIERS, matching the overlay
    # vocabulary test: an assertion derived from the same constant as the
    # message would survive the vocabulary changing.
    assert "tier: low | medium | high | maximum" in violations[0]


# --- The tier vocabulary reaches the overlay too (basicly-axqe) ----------------

_OVERLAY_AGENTS_DIR = ".basicly-local/agents"


def test_an_unknown_tier_in_the_agents_overlay_fails_the_catalog_lint(tmp_path: Path) -> None:
    """The asymmetry this rule closes: schema validation globs core only.

    Measured before the fix — a core source with `tier: turbo` was rejected while
    the same source in the overlay was accepted silently. All three things the
    author needs are asserted: the file, the value they typed, and the vocabulary
    that would have been accepted.
    """
    root = _catalog(tmp_path)
    _agent_source(root, "reviewer", "tier: turbo\n", _OVERLAY_AGENTS_DIR)

    violations = [v for v in lint_catalog(root) if "reviewer" in v]

    assert len(violations) == 1, f"one defect must yield one diagnostic: {violations}"
    assert ".basicly-local/agents/reviewer/agent.yaml" in violations[0]
    assert "'turbo'" in violations[0]
    # Spelled out rather than joined from MODEL_TIERS: an assertion derived from
    # the same constant as the message would survive the vocabulary changing.
    assert "tier: low | medium | high | maximum" in violations[0]


def test_a_valid_tier_in_the_agents_overlay_passes_the_catalog_lint(tmp_path: Path) -> None:
    """The check must accept the vocabulary, not merely reject outside it."""
    root = _catalog(tmp_path)
    _agent_source(root, "reviewer", "tier: high\n", _OVERLAY_AGENTS_DIR)

    assert lint_catalog(root) == []


def test_a_non_string_tier_in_the_agents_overlay_is_flagged(tmp_path: Path) -> None:
    """`tier: 0` is a typo, not an absence, and must not read as "no tier declared"."""
    root = _catalog(tmp_path)
    _agent_source(root, "reviewer", "tier: 0\n", _OVERLAY_AGENTS_DIR)

    violations = [v for v in lint_catalog(root) if "reviewer" in v]

    assert len(violations) == 1, violations
    assert "model tier 0 is not in the portable vocabulary" in violations[0]


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("- not a mapping\n", "agent source must be a YAML mapping"),
        ("name: [unclosed\n", "invalid YAML"),
    ],
)
def test_a_malformed_overlay_agent_source_lints_as_one_violation(
    tmp_path: Path, content: str, expected: str
) -> None:
    """A source with no readable tier must fail the gate, and fail it exactly once.

    The agent lint already reports both shapes (it loads the same overlay root),
    so the tier check stays silent rather than adding a second diagnostic for one
    defect — and a crash never stands in for the lint failure.
    """
    root = _catalog(tmp_path)
    path = root / _OVERLAY_AGENTS_DIR / "reviewer" / "agent.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(content, encoding="utf-8")

    violations = [v for v in lint_catalog(root) if "reviewer" in v]

    assert len(violations) == 1, violations
    assert expected in violations[0]


# --- One run must spell one source one way (basicly-ky5z) ----------------------


def test_a_load_time_failure_reports_the_same_path_style_as_the_lint_walk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bad technology value is reported twice: at load time and by the vocabulary walk.

    The load-time `ValidationError` used to render the absolute source path while the
    walk rendered a repo-relative one, so a single run leaked a home directory into
    whatever a reader pasted into an issue or a CI log. `lint_catalog` takes the root as
    an argument but the error does not, so it falls back to the working directory -
    which is what `cli._repo_root` means by the repo root.
    """
    root = _catalog(tmp_path)
    _agent_source(root, "reviewer", "technologies: [notatechnology]\n")
    monkeypatch.chdir(root)

    violations = [v for v in lint_catalog(root) if "notatechnology" in v]

    assert len(violations) == 2, f"both the load and the walk must report it: {violations}"
    assert violations[0] == violations[1]
    assert violations[0].startswith(".basicly/core/agents/reviewer/agent.yaml: ")
    assert str(root) not in violations[0]


def test_no_skill_description_names_a_phase_the_engine_does_not_have() -> None:
    """A description is the router, so a phantom phase in one is a false claim.

    `harness-loop` advertised `teardown` and `retro` until 2026-08-09
    (basicly-u2hl.44). Neither is in ``loop_state.PHASES``: teardown is folded into
    the ship advance and the retro is a tracker comment, so `basicly loop status`
    can never report either. A description is what an agent reads to decide whether
    to load the skill at all, which makes a phase it cannot reach a discoverability
    defect rather than a wording nit.

    Deliberately narrow. It fires only on a name used *as a phase* — inside an arrow
    chain — because the words themselves are ordinary English and a skill is free to
    discuss a retro. Broadening it to any mention would make it unfixable prose
    policing rather than a claim check.
    """
    known = set(loop_state.PHASES)
    offenders: list[str] = []
    for skill in discover_skills(Path.cwd()):
        for chain in re.findall(r"\(([^()]*→[^()]*)\)", skill.description):
            named = {step.strip().strip("`") for step in chain.split("→")}
            phantom = sorted(name for name in named if name and name not in known)
            if phantom:
                offenders.append(f"{skill.slug}: {', '.join(phantom)}")

    assert offenders == [], (
        f"skill description(s) name a phase the engine does not have: {offenders}; "
        f"loop_state.PHASES is {sorted(known)}"
    )


def test_the_phase_check_would_catch_a_phantom() -> None:
    """The positive control: the assertion above discriminates.

    Without it the check reads identically whether it is enforcing something or
    matching nothing at all.
    """
    known = set(loop_state.PHASES)
    chain = "intake → classify → build → teardown"
    named = {step.strip() for step in chain.split("→")}

    assert sorted(name for name in named if name not in known) == ["teardown"]


def test_the_listing_budget_warning_reports_the_arithmetic(tmp_path: Path) -> None:
    """Over budget warns with the numbers, because "over budget" cannot be acted on.

    The reader needs the entry count, the token total and the budget to decide
    whether to cut one long description or three dead skills.
    """
    root = _catalog(tmp_path)
    for index in range(40):
        _skill_source(
            root,
            f"filler-{index}",
            f"schema_version: 1\nname: filler-{index}\ninvocation: model\n"
            f'description: "{"x" * 400}"\n{_INSTRUCTIONS}',
        )

    warnings = listing_budget_warnings(root)

    entries = sum(1 for skill in discover_skills(root) if skill.invocation == "model")

    assert len(warnings) == 1
    assert "skill listing is" in warnings[0]
    assert "token budget" in warnings[0]
    # Derived, not literal: the fixture seeds a skill of its own, and a hardcoded
    # count would break on a change to the fixture rather than to the subject.
    assert f"{entries} model-invoked entries" in warnings[0]
    assert "least-invoked first" in warnings[0]


def test_the_listing_budget_is_silent_when_it_fits(tmp_path: Path) -> None:
    """The positive control: the warning above must not fire on a small catalog.

    Without it a warning emitted unconditionally would satisfy the assertions above
    and discriminate nothing — the failure this repo has shipped before.
    """
    root = _catalog(tmp_path)
    _skill_source(
        root,
        "small",
        f"schema_version: 1\nname: small\ninvocation: model\n"
        f"description: Does one thing. Use when that thing.\n{_INSTRUCTIONS}",
    )

    assert listing_budget_warnings(root) == []


def test_a_user_invoked_entry_costs_nothing_in_the_listing(tmp_path: Path) -> None:
    """The saving the invocation axis exists to buy, asserted rather than assumed.

    A user-invoked source carries no description, so it contributes only its name —
    which is the whole argument for the axis and is worth a check, since a
    regression here would look like the catalog simply growing.
    """
    root = _catalog(tmp_path)
    for index in range(40):
        _skill_source(
            root,
            f"filler-{index}",
            f"schema_version: 1\nname: filler-{index}\ninvocation: model\n"
            f'description: "{"x" * 400}"\n{_INSTRUCTIONS}',
        )
    over = listing_budget_warnings(root)

    for index in range(40):
        _skill_source(
            root,
            f"filler-{index}",
            f"schema_version: 1\nname: filler-{index}\ninvocation: user\n{_INSTRUCTIONS}",
        )

    assert over != []
    assert listing_budget_warnings(root) == []
