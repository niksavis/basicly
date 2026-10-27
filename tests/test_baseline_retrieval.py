from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader

from basicly import __version__
from basicly.loader import load_fragments, load_targets
from basicly.planner import plan_outputs
from basicly.renderers import claude, codex, copilot
from basicly.schema import Fragment

ROOT = Path(__file__).parent.parent
TARGETS = ("AGENTS.md", ".claude/CLAUDE.md", ".github/copilot-instructions.md")
CUSTOMER_LINES = 50
CORE = ROOT / ".basicly/core"
RENDERERS = {"claude": claude, "codex": codex, "copilot": copilot}
ROUTES = {
    "External Review": ("third-party project", "falsify-first"),
    "Model Tier Routing": ("specialist dispatch", "tier-injection"),
    "Rendered Surfaces": ("rendered page", "validate-as-consumer"),
}


@pytest.mark.parametrize("target", TARGETS)
def test_constraint_groups_precede_commands_and_the_workflow(target: str) -> None:
    text = (ROOT / target).read_text()
    assert re.findall(r"(?m)^## (.+)$", text) == ["Never do", "Ask first", "Commands", "Always do"]
    workflow = [
        "Knowledge Priming",
        "Decision Protocol",
        "Core Rules",
        "Quality Gate",
        "Git Discipline",
        "Session Completion",
    ]
    positions = [text.index(f"### {title}\n") for title in workflow]
    assert positions == sorted(positions)
    assert text.index("### Require Explicit Confirmation") > text.index("## Ask first")
    assert text.index("### Secure Coding") > text.index("## Never do")
    assert text.count("never defeat a gate") == 1


@pytest.mark.parametrize("target", TARGETS)
def test_specialist_triggers_reach_real_skills_without_host_scope_discovery(target: str) -> None:
    text = (ROOT / target).read_text()
    for heading, (trigger, skill) in ROUTES.items():
        section = text.split(f"### {heading}\n", 1)[1].split("\n### ", 1)[0]
        path = f".agents/skills/{skill}/SKILL.md"
        assert trigger in section and skill in section
        assert (ROOT / path).is_file()
        assert (ROOT / f".claude/skills/{skill}/SKILL.md").is_file()
    assert "no-comments" in text
    assert "test-discipline" in text


@pytest.mark.parametrize("target", TARGETS)
def test_absence_controls_stay_always_on_and_risk_controls_route_to_falsify_first(
    target: str,
) -> None:
    text = (ROOT / target).read_text()
    assert "known-match control through the same search" in text
    assert "match must increase the examined count" in text
    facts = text.split("### External Facts\n", 1)[1].split("\n### ", 1)[0]
    assert "high-risk number" in facts and "falsify-first" in facts
    skill = (ROOT / ".agents/skills/falsify-first/SKILL.md").read_text()
    assert "two paths that share no calculation step" in skill
    assert "name the list that the code reads" in skill
    assert "account for every original file" in skill


def test_prior_instruction_inventory_has_a_destination_for_every_unit() -> None:
    audit = json.loads(
        (ROOT / "docs/design/basicly-a3ab.4-instruction-accounting.json").read_text()
    )
    sources = audit["sources"]
    assert len(sources) == 24
    assert len({source["id"] for source in sources}) == len(sources)
    for source in sources:
        assert len(source["before_body_sha256"]) == 64
        assert source["before_unit_count"] == len(source["units"])
        assert [unit["unit"] for unit in source["units"]] == list(
            range(1, source["before_unit_count"] + 1)
        )
        for unit in source["units"]:
            assert unit["disposition"] in {"retained", "deduplicated", "routed", "corrected"}
            assert len(unit["sha256"]) == 64 and unit["reason"]
            assert (ROOT / unit["destination"].split("#", 1)[0]).is_file()


def test_custom_untagged_catalog_keeps_flat_layout() -> None:
    env = Environment(loader=FileSystemLoader(ROOT / ".basicly/core/templates"))
    fragment = Fragment(
        id="custom",
        description="Custom rule",
        category="design",
        applies_to=["all"],
        title="Custom",
        body="- rule",
    )
    text = env.get_template("codex/agents_md.j2").render(header="header\n", fragments=[fragment])
    assert "## Custom\n" in text
    assert "## Always do" not in text


def test_decision_grader_rejects_missing_answers_and_wrong_actions() -> None:
    path = ROOT / ".scripts/eval_baseline_decisions.py"
    spec = importlib.util.spec_from_file_location("baseline_decisions", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cases = json.loads((ROOT / "tests/fixtures/baseline-decisions.json").read_text())
    right = {case["id"]: case["expected"] for case in cases}
    assert all(module.grade(cases, json.dumps(right)).values())
    assert all(module.grade(cases, "tool progress\n" + json.dumps(right)).values())
    wrong = dict.fromkeys(right, "Z")
    assert not any(module.grade(cases, json.dumps(wrong)).values())
    with pytest.raises(ValueError, match="exactly the scenario ids"):
        module.grade(cases, "{}")
    with pytest.raises(ValueError):
        module.grade(cases, "not json")


def test_the_shipped_core_leaves_a_consumer_fifty_lines_of_the_vendor_cap() -> None:
    targets = load_targets(CORE / "targets")
    fragments = load_fragments(CORE / "fragments", {target.name for target in targets})
    lines = {
        item.output_name: len(
            RENDERERS[item.target_name].render(item, CORE / "templates", __version__).splitlines()
        )
        for item in plan_outputs(fragments, targets, ROOT)
        if item.output_name in {"claude_wrapper", "agents_baseline", "copilot_baseline"}
    }
    assert len(lines) == 3
    assert all(count <= 200 - CUSTOMER_LINES for count in lines.values()), lines
