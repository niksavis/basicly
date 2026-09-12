from __future__ import annotations

from pathlib import Path

from basicly import skill_pairing
from basicly.agents import lint_agent_sources
from basicly.skill_source import MODEL_INVOKED, USER_INVOKED, discover_skills
from tests.agent_helpers import _agent_yaml, _write_agent

REPO_ROOT = Path(__file__).resolve().parents[1]
_UNPAIRED = "declared by no agent"


def _write_skill(tmp_path: Path, slug: str, invocation: str) -> None:
    path = tmp_path / ".basicly" / "core" / "skills" / slug / "skill.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    described = f"description: what {slug} is for\n" if invocation == MODEL_INVOKED else ""
    path.write_text(
        f"schema_version: 1\nname: {slug}\ninvocation: {invocation}\n{described}"
        "instructions: |\n  # x\n",
        encoding="utf-8",
    )


def _catalog(tmp_path: Path) -> Path:
    _write_agent(
        tmp_path / ".basicly" / "core" / "agents",
        "probe",
        _agent_yaml("probe", extra="claude:\n  skills: [paired]\n"),
    )
    _write_skill(tmp_path, "paired", MODEL_INVOKED)
    return tmp_path


def test_a_model_invoked_skill_no_agent_declares_is_refused(tmp_path: Path) -> None:

    _write_skill(_catalog(tmp_path), "orphan", MODEL_INVOKED)

    violations = lint_agent_sources(tmp_path)

    assert any(_UNPAIRED in v and "'orphan'" in v for v in violations), violations
    assert any(".basicly/core/skills/orphan/skill.yaml" in v for v in violations), violations
    assert any("claude.skills" in v and "UNPAIRED_EXEMPTIONS" in v for v in violations), violations
    assert not any("'paired'" in v for v in violations), violations


def test_a_user_invoked_skill_no_agent_declares_is_not_reported(tmp_path: Path) -> None:

    _write_skill(_catalog(tmp_path), "tool-probe", USER_INVOKED)

    assert not any(_UNPAIRED in v for v in lint_agent_sources(tmp_path))


def test_an_exempt_model_invoked_skill_is_not_reported(tmp_path: Path) -> None:
    exempt, *_ = sorted(skill_pairing.UNPAIRED_EXEMPTIONS)
    _write_skill(_catalog(tmp_path), exempt, MODEL_INVOKED)

    assert not any(f"'{exempt}'" in v for v in lint_agent_sources(tmp_path))


def test_a_catalog_with_no_agents_is_not_reported_as_fourteen_gaps(tmp_path: Path) -> None:

    _write_skill(tmp_path, "lonely", MODEL_INVOKED)

    assert not any(_UNPAIRED in v for v in lint_agent_sources(tmp_path))


def test_declared_skill_names_reads_both_shapes_the_host_accepts() -> None:
    assert skill_pairing.declared_skill_names((("skills", ["a", "b"]),)) == ("a", "b")
    assert skill_pairing.declared_skill_names((("skills", "a"),)) == ("a",)
    assert skill_pairing.declared_skill_names((("effort", "high"),)) == ()
    assert skill_pairing.declared_skill_names((("skills", 7),)) == ()


def test_every_exemption_names_a_model_invoked_skill_in_this_catalog() -> None:
    model_invoked = {s.slug for s in discover_skills(REPO_ROOT) if s.invocation == MODEL_INVOKED}
    assert model_invoked, "positive control: this catalog must hold model-invoked skills"
    assert set(skill_pairing.UNPAIRED_EXEMPTIONS) <= model_invoked


def test_this_catalog_pairs_or_exempts_every_model_invoked_skill() -> None:
    assert [v for v in lint_agent_sources(REPO_ROOT) if _UNPAIRED in v] == []
    assert all(reason.strip() for reason in skill_pairing.UNPAIRED_EXEMPTIONS.values())
