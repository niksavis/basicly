from __future__ import annotations

from pathlib import Path

from basicly import skill_coverage
from basicly.skill_source import SKILLS_SOURCE_DIR, SkillDefinition, discover_skills

REPO_ROOT = Path(__file__).resolve().parents[1]


def _skill(name: str, work_types: tuple[str, ...] = (), phases: tuple[str, ...] = ()):
    return SkillDefinition(
        slug=name,
        name=name,
        invocation="model",
        description="d",
        instructions="i",
        source_path=Path(f"{name}/skill.yaml"),
        covered_work_types=work_types,
        covered_phases=phases,
    )


def _write_source(root: Path, slug: str, covers: str) -> None:
    path = root / SKILLS_SOURCE_DIR / slug / "skill.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# yaml-language-server: $schema=../../schemas/skill.schema.json\n"
        f"schema_version: 1\nname: {slug}\ninvocation: model\ndescription: d\n"
        f"{covers}instructions: |\n  # {slug}\n",
        encoding="utf-8",
    )


def test_an_empty_axis_means_any_on_that_axis() -> None:
    skills = [_skill("phase-only", phases=("build",)), _skill("type-only", work_types=("bug",))]
    assert skill_coverage.covering_skills(skills, "bug", "build") == ("phase-only", "type-only")
    assert skill_coverage.covering_skills(skills, "chore", "build") == ("phase-only",)
    assert skill_coverage.covering_skills(skills, "bug", "ship") == ("type-only",)


def test_both_axes_must_match_when_both_are_declared() -> None:
    skills = [_skill("both", work_types=("bug",), phases=("build", "repair"))]
    assert skill_coverage.covering_skills(skills, "bug", "repair") == ("both",)
    assert skill_coverage.covering_skills(skills, "bug", "ship") == ()
    assert skill_coverage.covering_skills(skills, "chore", "build") == ()


def test_a_skill_declaring_nothing_is_never_named() -> None:
    assert skill_coverage.covering_skills([_skill("silent")], "bug", "build") == ()


def test_an_absent_work_type_narrows_rather_than_widens() -> None:
    skills = [_skill("typed", work_types=("bug",)), _skill("phased", phases=("build",))]
    assert skill_coverage.covering_skills(skills, None, "build") == ("phased",)
    assert skill_coverage.covering_skills(skills, None, None) == ()


def test_a_phase_the_engine_cannot_dispatch_is_refused_by_the_lint() -> None:
    good = _skill("ok", work_types=("bug",), phases=("build",))
    bad = _skill("typo", work_types=("docs",), phases=("deploy",))
    assert skill_coverage.vocabulary_problems([good]) == []
    problems = skill_coverage.vocabulary_problems([bad])
    assert len(problems) == 2
    assert "covers.work_types names docs" in problems[0]
    assert "covers.phases names deploy" in problems[1]


def test_the_coverable_phases_are_the_ones_that_resolve_a_persona() -> None:
    assert {"build", "repair", "validate", "decompose"} <= skill_coverage.COVERABLE_PHASES
    assert "verify" not in skill_coverage.COVERABLE_PHASES


def test_the_unit_route_reads_the_source_because_covers_is_never_projected(
    tmp_path: Path,
) -> None:
    _write_source(tmp_path, "reachable", "covers:\n  phases: [build]\n")
    _write_source(tmp_path, "undeclared", "")
    assert skill_coverage.unit_skills(tmp_path, "bug", "build") == ("reachable",)
    assert skill_coverage.unit_skills(tmp_path, "bug", "ship") == ()


def test_a_malformed_source_costs_the_unit_route_and_not_the_dispatch(tmp_path: Path) -> None:
    path = tmp_path / SKILLS_SOURCE_DIR / "broken" / "skill.yaml"
    path.parent.mkdir(parents=True)
    path.write_text("name: broken\n", encoding="utf-8")
    assert skill_coverage.unit_skills(tmp_path, "bug", "build") == ()


def test_an_unknown_family_declares_nothing_rather_than_raising(tmp_path: Path) -> None:
    assert skill_coverage.role_skills(tmp_path, "codex", "implementer") == ()
    assert skill_coverage.role_skills(tmp_path, "claude", "no-such-role") == ()


def test_the_declared_list_stops_at_the_next_frontmatter_key() -> None:
    text = "---\nname: r\nskills:\n- one\n- two\ntools: Read\n---\n\n- not-a-skill\n"
    assert skill_coverage.declared_skills(text) == ("one", "two")


def test_a_never_invoked_skill_is_split_by_whether_a_dispatch_delivers_it() -> None:

    named = ("root-cause", "worktree-isolation", "repair-in-place", "tool-jq")
    split = skill_coverage.partition_never_invoked(REPO_ROOT, named)
    assert split.unreachable == ("tool-jq",)
    assert split.delivered == ("root-cause", "worktree-isolation", "repair-in-place")


def test_every_skill_the_record_names_is_reachable_by_some_dispatch() -> None:
    six = (
        "root-cause",
        "falsify-first",
        "repair-in-place",
        "validate-as-consumer",
        "worktree-isolation",
        "decompose-plan",
    )
    assert skill_coverage.unreachable_skills(REPO_ROOT, six) == ()


def test_every_declared_cover_names_a_real_skill_in_this_catalog() -> None:
    declared = [s for s in discover_skills(REPO_ROOT) if s.covered_work_types or s.covered_phases]
    assert {s.name for s in declared} == {s.slug for s in declared}
    assert len(declared) == 6
