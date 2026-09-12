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
    "schema_version: 1\nname: s\ninvocation: model\ndescription: d\n"
    "token_cost:\n  listing: 6\ninstructions: |\n  body\n"
)
VALID_FRAGMENT = (
    "schema_version: 1\nid: f\ndescription: d\ncategory: project\n"
    "token_cost: {claude: 0, codex: 0, copilot: 0}\napplies_to: [all]\nbody: |\n  - x\n"
)


def _catalog(tmp_path: Path) -> Path:
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
    assert lint_catalog(_catalog(tmp_path)) == []


def test_flags_skill_md_source(tmp_path: Path) -> None:
    root = _catalog(tmp_path)
    (root / ".basicly/core/skills/legacy").mkdir()
    (root / ".basicly/core/skills/legacy/SKILL.md").write_text("x\n", encoding="utf-8")
    assert any("SKILL.md" in v for v in lint_catalog(root))


def test_flags_fragment_md_source(tmp_path: Path) -> None:
    root = _catalog(tmp_path)
    (root / ".basicly/core/fragments/project/legacy.fragment.md").write_text(
        "x\n", encoding="utf-8"
    )
    assert any("fragment.md" in v for v in lint_catalog(root))


def test_flags_yml_extension(tmp_path: Path) -> None:
    root = _catalog(tmp_path)
    (root / ".basicly/core/stray.yml").write_text("a: 1\n", encoding="utf-8")
    assert any(".yml" in v for v in lint_catalog(root))


def test_flags_schema_violation(tmp_path: Path) -> None:
    root = _catalog(tmp_path)
    (root / ".basicly/core/skills/s/skill.yaml").write_text(
        "schema_version: 1\nname: s\ninvocation: model\ndescription: d\n", encoding="utf-8"
    )
    violations = lint_catalog(root)
    assert any("skill.yaml" in v for v in violations)


def test_enforced_by_cited_in_body_passes(tmp_path: Path) -> None:
    root = _catalog(tmp_path)
    (root / ".basicly/core/fragments/project/f.fragment.yaml").write_text(
        "schema_version: 1\nid: f\ndescription: d\ncategory: code-style\n"
        "applies_to: [all]\nenforced_by: [ruff format]\n"
        "body: |\n  Formatting is enforced by `ruff format`.\n",
        encoding="utf-8",
    )
    assert lint_catalog(root) == []


def test_enforced_by_not_cited_is_flagged(tmp_path: Path) -> None:
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
    root = _catalog(tmp_path)
    assert not any("enforced_by" in v for v in lint_catalog(root))


def test_valid_technologies_pass(tmp_path: Path) -> None:
    root = _catalog(tmp_path)
    (root / ".basicly/core/skills/s/skill.yaml").write_text(
        VALID_SKILL.replace("description: d\n", "description: d\ntechnologies: [python]\n"),
        encoding="utf-8",
    )
    (root / ".basicly/core/fragments/project/f.fragment.yaml").write_text(
        VALID_FRAGMENT.replace("applies_to: [all]\n", "applies_to: [all]\ntechnologies: [zsh]\n"),
        encoding="utf-8",
    )
    assert lint_catalog(root) == []


def test_flags_unknown_technology(tmp_path: Path) -> None:
    root = _catalog(tmp_path)
    (root / ".basicly/core/skills/s/skill.yaml").write_text(
        "schema_version: 1\nname: s\ninvocation: model\ndescription: d\ntechnologies: [cobol]\n"
        "instructions: |\n  body\n",
        encoding="utf-8",
    )
    violations = lint_catalog(root)
    assert any("unknown technologies: cobol" in v for v in violations)


def test_flags_unknown_technology_in_hooks_manifest(tmp_path: Path) -> None:
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
    root = _catalog(tmp_path)
    body = "\n".join(f"  line {n}" for n in range(600))
    skill = root / ".basicly/core/skills/big/skill.yaml"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        f"schema_version: 1\nname: big\ninvocation: model\ndescription: d\n"
        f"token_cost:\n  listing: 6\ninstructions: |\n{body}\n",
        encoding="utf-8",
    )
    assert lint_catalog(root) == []
    assert any("keep it under" in w for w in skill_warnings(root))


def test_deep_file_reference_warns(tmp_path: Path) -> None:
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
    root = _catalog(tmp_path)
    skill = root / ".basicly/core/skills/refs/skill.yaml"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "schema_version: 1\nname: refs\ninvocation: model\ndescription: d\ninstructions: |\n"
        "  See [the guide](references/guide.md) and run scripts/fix.sh.\n",
        encoding="utf-8",
    )
    assert not any("more than one level deep" in w for w in skill_warnings(root))


def _skill_source(root: Path, slug: str, body: str) -> Path:
    path = root / ".basicly/core/skills" / slug / "skill.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


_INSTRUCTIONS = "token_cost:\n  listing: 6\ninstructions: |\n  # x\n\n  text\n"


def test_a_missing_invocation_declaration_fails_the_lint(tmp_path: Path) -> None:
    root = _catalog(tmp_path)
    _skill_source(root, "undeclared", f"schema_version: 1\nname: undeclared\n{_INSTRUCTIONS}")

    violations = lint_catalog(root)

    assert any("no 'invocation' declared" in v for v in violations), violations


def test_a_missing_invocation_names_both_values_and_the_safe_migration(tmp_path: Path) -> None:

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
    root = _catalog(tmp_path)
    _skill_source(
        root,
        "bogus",
        f"schema_version: 1\nname: bogus\ninvocation: sometimes\ndescription: d\n{_INSTRUCTIONS}",
    )

    assert any("sometimes" in v for v in lint_catalog(root))


def test_a_user_invoked_entry_carrying_a_description_fails_the_lint(tmp_path: Path) -> None:
    root = _catalog(tmp_path)
    _skill_source(
        root,
        "handrun",
        f"schema_version: 1\nname: handrun\ninvocation: user\ndescription: d\n{_INSTRUCTIONS}",
    )

    violations = [v for v in lint_catalog(root) if "handrun" in v]

    assert any("must not carry a description" in v for v in violations), violations


def test_a_model_invoked_entry_without_a_description_fails_the_lint(tmp_path: Path) -> None:
    root = _catalog(tmp_path)
    _skill_source(
        root, "silent", f"schema_version: 1\nname: silent\ninvocation: model\n{_INSTRUCTIONS}"
    )

    violations = [v for v in lint_catalog(root) if "silent" in v]

    assert any("needs a description" in v for v in violations), violations


def test_every_shipped_skill_declares_the_axis() -> None:
    for path in sorted((REPO / ".basicly/core/skills").glob("*/skill.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert data.get("invocation") in {"model", "user"}, f"{path.parent.name} has no axis"


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
        f"posture: Read-only.\ntools: [Read, Grep, Glob]\n{extra}"
        f"claude:\n  skills: [s]\nslots:\n{_AGENT_SLOTS}",
        encoding="utf-8",
    )
    return path


def test_a_declared_model_tier_passes_the_catalog_lint(tmp_path: Path) -> None:
    root = _catalog(tmp_path)
    _agent_source(root, "reviewer", "tier: high\n")

    assert lint_catalog(root) == []


def test_an_agent_pinning_a_model_names_the_source_and_the_tier_field(tmp_path: Path) -> None:

    root = _catalog(tmp_path)
    _agent_source(root, "reviewer", "model: haiku\n")

    violations = [v for v in lint_catalog(root) if "reviewer" in v]

    assert len(violations) == 1, f"one defect must yield one diagnostic: {violations}"
    assert ".basicly/core/agents/reviewer/agent.yaml" in violations[0]
    assert "tier: low | medium | high | maximum" in violations[0]
    assert "not allowed" not in violations[0]


def test_no_shipped_agent_source_declares_a_model() -> None:
    for path in sorted((REPO / ".basicly/core/agents").glob("*/agent.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert "model" not in data, f"{path.parent.name} pins a provider model id"


def test_every_shipped_agent_source_declares_a_model_tier() -> None:

    for path in sorted((REPO / ".basicly/core/agents").glob("*/agent.yaml")):
        tier = yaml.safe_load(path.read_text(encoding="utf-8")).get("tier")
        assert tier in MODEL_TIERS, f"{path.parent.name} declares no usable model tier: {tier!r}"


def test_the_agent_schema_tier_enum_matches_the_model_tier_vocabulary() -> None:
    schema = json.loads(
        (REPO / ".basicly/core/schemas/agent.schema.json").read_text(encoding="utf-8")
    )
    assert schema["properties"]["tier"]["enum"] == list(MODEL_TIERS)


def test_the_agent_schema_requires_a_tier() -> None:
    schema = json.loads(
        (REPO / ".basicly/core/schemas/agent.schema.json").read_text(encoding="utf-8")
    )
    assert "tier" in schema["required"]


@pytest.mark.parametrize("agents_dir", [".basicly/core/agents", ".basicly-local/agents"])
def test_an_agent_source_declaring_no_tier_fails_the_catalog_lint(
    tmp_path: Path, agents_dir: str
) -> None:

    root = _catalog(tmp_path)
    _agent_source(root, "reviewer", "", agents_dir)

    violations = [v for v in lint_catalog(root) if "reviewer" in v]

    assert len(violations) == 1, f"one defect must yield one diagnostic: {violations}"
    assert f"{agents_dir}/reviewer/agent.yaml" in violations[0]
    assert "tier: low | medium | high | maximum" in violations[0]


_OVERLAY_AGENTS_DIR = ".basicly-local/agents"


def test_an_unknown_tier_in_the_agents_overlay_fails_the_catalog_lint(tmp_path: Path) -> None:

    root = _catalog(tmp_path)
    _agent_source(root, "reviewer", "tier: turbo\n", _OVERLAY_AGENTS_DIR)

    violations = [v for v in lint_catalog(root) if "reviewer" in v]

    assert len(violations) == 1, f"one defect must yield one diagnostic: {violations}"
    assert ".basicly-local/agents/reviewer/agent.yaml" in violations[0]
    assert "'turbo'" in violations[0]
    assert "tier: low | medium | high | maximum" in violations[0]


def test_a_valid_tier_in_the_agents_overlay_passes_the_catalog_lint(tmp_path: Path) -> None:
    root = _catalog(tmp_path)
    _agent_source(root, "reviewer", "tier: high\n", _OVERLAY_AGENTS_DIR)

    assert lint_catalog(root) == []


def test_a_non_string_tier_in_the_agents_overlay_is_flagged(tmp_path: Path) -> None:
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

    root = _catalog(tmp_path)
    path = root / _OVERLAY_AGENTS_DIR / "reviewer" / "agent.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(content, encoding="utf-8")

    violations = [v for v in lint_catalog(root) if "reviewer" in v]

    assert len(violations) == 1, violations
    assert expected in violations[0]


def test_a_load_time_failure_reports_the_same_path_style_as_the_lint_walk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    root = _catalog(tmp_path)
    _agent_source(root, "reviewer", "technologies: [notatechnology]\n")
    monkeypatch.chdir(root)

    violations = [v for v in lint_catalog(root) if "notatechnology" in v]

    assert len(violations) == 2, f"both the load and the walk must report it: {violations}"
    assert violations[0] == violations[1]
    assert violations[0].startswith(".basicly/core/agents/reviewer/agent.yaml: ")
    assert str(root) not in violations[0]


def test_no_skill_description_names_a_phase_the_engine_does_not_have() -> None:

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

    known = set(loop_state.PHASES)
    chain = "intake → classify → build → teardown"
    named = {step.strip() for step in chain.split("→")}

    assert sorted(name for name in named if name not in known) == ["teardown"]


def test_the_listing_budget_warning_reports_the_arithmetic(tmp_path: Path) -> None:

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
    assert f"{entries} model-invoked entries" in warnings[0]
    assert "least-invoked first" in warnings[0]


def test_the_listing_budget_is_silent_when_it_fits(tmp_path: Path) -> None:

    root = _catalog(tmp_path)
    _skill_source(
        root,
        "small",
        f"schema_version: 1\nname: small\ninvocation: model\n"
        f"description: Does one thing. Use when that thing.\n{_INSTRUCTIONS}",
    )

    assert listing_budget_warnings(root) == []


def test_a_user_invoked_entry_costs_nothing_in_the_listing(tmp_path: Path) -> None:

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


def _style_source(root: Path, body: str) -> Path:
    path = root / ".basicly/core/output-styles/s/style.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(body, encoding="utf-8")
    return path


def test_a_catalog_with_no_output_style_needs_no_output_style_schema(tmp_path: Path) -> None:
    root = _catalog(tmp_path)
    assert not (root / ".basicly/core/schemas/output-style.schema.json").exists()

    assert lint_catalog(root) == []


def test_an_output_style_source_is_validated_against_its_schema(tmp_path: Path) -> None:
    root = _catalog(tmp_path)
    (root / ".basicly/core/schemas/output-style.schema.json").write_text(
        (REPO / ".basicly/core/schemas/output-style.schema.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    _style_source(root, "schema_version: 1\nname: S\ndescription: d\nbody: |\n  text\nextra: 1\n")

    assert any("'extra' was unexpected" in v for v in lint_catalog(root))


def test_a_markdown_output_style_source_is_refused(tmp_path: Path) -> None:
    root = _catalog(tmp_path)
    path = root / ".basicly/core/output-styles/s/style.md"
    path.parent.mkdir(parents=True)
    path.write_text("---\nname: S\n---\n", encoding="utf-8")

    assert any("output style sources must be style.yaml" in v for v in lint_catalog(root))
