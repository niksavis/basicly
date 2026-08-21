"""The other direction of the agent/skill pairing: a shipped skill no agent declares.

``test_agent_lint`` covers the declaration side — a name that resolves to nothing. This
covers the population side: a model-invoked skill the catalog ships and no role carries,
which reaches no dispatch prompt at all (basicly-sromom). The rule is reported through
``agents.lint_agent_sources``, so it is exercised through that entry point rather than
through :func:`~basicly.skill_pairing.unpaired_skills` alone.
"""

from __future__ import annotations

from pathlib import Path

from basicly import skill_pairing
from basicly.agents import lint_agent_sources
from basicly.skill_source import MODEL_INVOKED, USER_INVOKED, discover_skills
from tests.agent_helpers import _agent_yaml, _write_agent

REPO_ROOT = Path(__file__).resolve().parents[1]
# The stable half of the message, so a reworded remedy does not silently stop matching.
_UNPAIRED = "declared by no agent"


def _write_skill(tmp_path: Path, slug: str, invocation: str) -> None:
    """One skill source, with a description only where the invocation axis allows one."""
    path = tmp_path / ".basicly" / "core" / "skills" / slug / "skill.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    described = f"description: what {slug} is for\n" if invocation == MODEL_INVOKED else ""
    path.write_text(
        f"schema_version: 1\nname: {slug}\ninvocation: {invocation}\n{described}"
        "instructions: |\n  # x\n",
        encoding="utf-8",
    )


def _catalog(tmp_path: Path) -> Path:
    """An agent declaring one real skill: the paired baseline every case starts from."""
    _write_agent(
        tmp_path / ".basicly" / "core" / "agents",
        "probe",
        _agent_yaml("probe", extra="claude:\n  skills: [paired]\n"),
    )
    _write_skill(tmp_path, "paired", MODEL_INVOKED)
    return tmp_path


def test_a_model_invoked_skill_no_agent_declares_is_refused(tmp_path: Path) -> None:
    """The engine inlines a declared skill, so an undeclared one is guidance nothing sends.

    Before this rule the gap was fourteen skills wide and only a hand count found it.
    """
    _write_skill(_catalog(tmp_path), "orphan", MODEL_INVOKED)

    violations = lint_agent_sources(tmp_path)

    assert any(_UNPAIRED in v and "'orphan'" in v for v in violations), violations
    # The violation has to carry the source it is about and both ways out of it, the
    # second named by the table a reader can actually open.
    assert any(".basicly/core/skills/orphan/skill.yaml" in v for v in violations), violations
    assert any("claude.skills" in v and "UNPAIRED_EXEMPTIONS" in v for v in violations), violations
    # The positive control: the declared skill is not reported, so the rule discriminates
    # rather than listing the catalog back.
    assert not any("'paired'" in v for v in violations), violations


def test_a_user_invoked_skill_no_agent_declares_is_not_reported(tmp_path: Path) -> None:
    """A ``tool-*`` entry carries no description and a human reaches it by typing its name.

    Reporting it would fire on 21 of this catalog's 41 entries permanently, which is how a
    gate gets waived wholesale instead of fixed.
    """
    _write_skill(_catalog(tmp_path), "tool-probe", USER_INVOKED)

    assert not any(_UNPAIRED in v for v in lint_agent_sources(tmp_path))


def test_an_exempt_model_invoked_skill_is_not_reported(tmp_path: Path) -> None:
    """The declared exemption is what holds the rule at zero without weakening it."""
    exempt, *_ = sorted(skill_pairing.UNPAIRED_EXEMPTIONS)
    _write_skill(_catalog(tmp_path), exempt, MODEL_INVOKED)

    assert not any(f"'{exempt}'" in v for v in lint_agent_sources(tmp_path))


def test_a_catalog_with_no_agents_is_not_reported_as_fourteen_gaps(tmp_path: Path) -> None:
    """With an empty roster every skill is trivially unpaired, which is a different fact.

    A consumer whose technology selection ships skills and no personas would otherwise
    get one violation per skill, none of them actionable.
    """
    _write_skill(tmp_path, "lonely", MODEL_INVOKED)

    assert not any(_UNPAIRED in v for v in lint_agent_sources(tmp_path))


def test_declared_skill_names_reads_both_shapes_the_host_accepts() -> None:
    """A list and a bare string are both valid ``skills:``; anything else is not a list."""
    assert skill_pairing.declared_skill_names((("skills", ["a", "b"]),)) == ("a", "b")
    assert skill_pairing.declared_skill_names((("skills", "a"),)) == ("a",)
    assert skill_pairing.declared_skill_names((("effort", "high"),)) == ()
    assert skill_pairing.declared_skill_names((("skills", 7),)) == ()


def test_every_exemption_names_a_model_invoked_skill_in_this_catalog() -> None:
    """An exemption matching nothing is dead config that still reads like a decision."""
    model_invoked = {s.slug for s in discover_skills(REPO_ROOT) if s.invocation == MODEL_INVOKED}
    assert model_invoked, "positive control: this catalog must hold model-invoked skills"
    assert set(skill_pairing.UNPAIRED_EXEMPTIONS) <= model_invoked


def test_this_catalog_pairs_or_exempts_every_model_invoked_skill() -> None:
    """The tree side: nothing is left unpaired, and every exemption states its reason."""
    assert [v for v in lint_agent_sources(REPO_ROOT) if _UNPAIRED in v] == []
    assert all(reason.strip() for reason in skill_pairing.UNPAIRED_EXEMPTIONS.values())
