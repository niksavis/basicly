from __future__ import annotations

from pathlib import Path

import pytest

from basicly import roles
from basicly.runner import BUILTIN_RUNNERS, format_command


def _spec(family: str):
    return next(spec for spec in BUILTIN_RUNNERS if spec.name == family)


def _project(repo_root: Path, family: str, role: str) -> None:
    root, suffix = roles.AGENT_ROOTS[family]
    target = repo_root / root / f"{role}{suffix}"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("---\nname: x\n---\n", encoding="utf-8")


def test_every_mapped_role_is_a_role_this_catalog_actually_ships() -> None:

    authored = {path.parent.name for path in Path(".basicly/core/agents").glob("*/agent.yaml")}

    mapped = set(roles.ROLE_BY_PHASE.values()) | set(roles.LENS_ROLE_BY_PHASE.values())

    unknown = sorted(mapped - authored)

    assert unknown == [], f"phase map names role(s) with no source: {unknown}"


def test_every_loop_role_is_reachable_from_one_of_the_phase_tables() -> None:

    loop_roles = {
        "decomposer",
        "implementer",
        "validator",
        "reviewer",
        "decider",
        "retrospector",
        "curator",
    }

    routed = set(roles.ROLE_BY_PHASE.values()) | set(roles.LENS_ROLE_BY_PHASE.values())

    assert sorted(loop_roles - routed) == []


def test_validate_resolves_to_more_than_one_role_and_each_review_carries_its_lens() -> None:

    assert roles.role_for_phase("validate") == "validator"

    reviews = roles.lens_dispatches("validate")

    assert [dispatch.role for dispatch in reviews] == ["reviewer"] * len(roles.REVIEW_LENSES)
    assert [dispatch.lens for dispatch in reviews] == list(roles.REVIEW_LENSES)
    assert len(roles.REVIEW_LENSES) == len(set(roles.REVIEW_LENSES))


def test_no_other_phase_pays_for_a_lens_dispatch() -> None:

    fanned_out = [phase for phase in roles.ROLE_BY_PHASE if roles.lens_dispatches(phase)]

    assert fanned_out == ["validate"]
    assert roles.lens_dispatches("verify") == ()


def test_a_named_role_resolves_only_when_its_family_can_load_it(tmp_path: Path) -> None:

    assert roles.resolve_named_role(tmp_path, _spec("claude"), "reviewer") is None

    _project(tmp_path, "claude", "reviewer")

    assert roles.resolve_named_role(tmp_path, _spec("claude"), "reviewer") == "reviewer"
    assert roles.resolve_named_role(tmp_path, _spec("codex"), "reviewer") is None


def test_a_superseded_role_resolves_to_the_role_that_replaced_it(tmp_path: Path) -> None:

    assert roles.resolve_named_role(tmp_path, _spec("claude"), "code-reviewer") is None

    _project(tmp_path, "claude", "reviewer")

    assert roles.resolve_named_role(tmp_path, _spec("claude"), "code-reviewer") == "reviewer"


def test_a_name_this_catalog_never_retired_survives_the_redirect_untouched(
    tmp_path: Path,
) -> None:

    _project(tmp_path, "claude", "implementer")

    assert roles.resolve_named_role(tmp_path, _spec("claude"), "implementer") == "implementer"
    assert roles.resolve_named_role(tmp_path, _spec("claude"), "implemnter") is None


def test_the_superseded_agent_is_gone_from_the_catalog_and_from_both_agent_roots() -> None:

    assert not Path(".basicly/core/agents/code-reviewer").exists()
    assert Path(".basicly/core/agents/reviewer/agent.yaml").is_file()

    for root, suffix in roles.AGENT_ROOTS.values():
        assert not (root / f"code-reviewer{suffix}").exists()
        assert (root / f"reviewer{suffix}").is_file()


def test_the_replacement_names_what_it_superseded_where_a_consumer_reads_it() -> None:

    for root, suffix in roles.AGENT_ROOTS.values():
        rendered = (root / f"reviewer{suffix}").read_text(encoding="utf-8")
        description = next(
            line for line in rendered.splitlines() if line.startswith("description:")
        )

        assert "code-reviewer" in description
        assert "supersedes" in description.lower()


def test_the_supersession_did_not_widen_the_lens_vocabulary() -> None:

    assert roles.REVIEW_LENSES == ("correctness", "security")


def test_a_phase_with_no_persona_resolves_to_nothing(tmp_path: Path) -> None:
    assert roles.role_for_phase("verify") is None
    assert roles.role_for_phase("done") is None
    assert roles.resolve_role(tmp_path, _spec("claude"), "verify") is None


def test_repair_resolves_to_the_implementer_because_it_is_a_mode() -> None:
    assert roles.role_for_phase("repair") == roles.role_for_phase("build") == "implementer"


def test_an_unprojected_role_resolves_to_nothing_rather_than_to_its_name(
    tmp_path: Path,
) -> None:

    assert roles.resolve_role(tmp_path, _spec("claude"), "build") is None

    _project(tmp_path, "claude", "implementer")

    assert roles.resolve_role(tmp_path, _spec("claude"), "build") == "implementer"


def test_a_family_with_no_subagent_root_never_resolves_a_role(tmp_path: Path) -> None:
    _project(tmp_path, "claude", "implementer")

    assert roles.resolve_role(tmp_path, _spec("codex"), "build") is None


@pytest.mark.parametrize("family", ["claude", "copilot"])
def test_a_resolved_role_reaches_the_argv(family: str) -> None:
    spec = next(spec for spec in BUILTIN_RUNNERS if spec.name == family)

    argv = format_command(spec, "PROMPT", role="implementer")

    assert argv[1:3] == ["--agent", "implementer"]
    assert "PROMPT" in argv


def test_codex_argv_is_unchanged_by_a_role_it_cannot_select() -> None:

    spec = next(spec for spec in BUILTIN_RUNNERS if spec.name == "codex")

    assert format_command(spec, "PROMPT", role="implementer") == format_command(spec, "PROMPT")


def test_no_role_leaves_every_family_exactly_as_it_was() -> None:
    for spec in BUILTIN_RUNNERS:
        if spec.command:
            assert format_command(spec, "PROMPT", role=None) == format_command(spec, "PROMPT")


def test_the_policy_never_names_a_role_the_engine_does_not_dispatch() -> None:
    dispatched = set(roles.ROLE_BY_PHASE.values()) | set(roles.LENS_ROLE_BY_PHASE.values())

    assert dispatched >= roles.INHERITING_ROLES


def test_the_implementer_inherits_and_repair_inherits_with_it() -> None:
    assert roles.inherits_context(roles.role_for_phase("build"))
    assert roles.inherits_context(roles.role_for_phase("repair"))


@pytest.mark.parametrize("phase", ["classify", "validate", "retrospective", "ship", "decompose"])
def test_a_judging_phase_stays_cold(phase: str) -> None:
    role = roles.role_for_phase(phase)

    assert role is not None
    assert not roles.inherits_context(role)


def test_the_reviewer_fanned_out_beside_validate_stays_cold_too() -> None:
    for dispatch in roles.lens_dispatches("validate"):
        assert not roles.inherits_context(dispatch.role)


def test_a_dispatch_with_no_role_at_all_starts_cold() -> None:
    assert not roles.inherits_context(roles.role_for_phase("verify"))
    assert not roles.inherits_context(None)
