"""Resolving a loop phase to the agent role that drives it (basicly-4kdm).

The gap this closes was measured twice and stated the same way each time: the
projection works and nothing consumes it. These assert the consuming half.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from basicly import roles
from basicly.runner import BUILTIN_RUNNERS, format_command


def _spec(family: str):
    """The real runner spec for *family* — the protocol's only production impl."""
    return next(spec for spec in BUILTIN_RUNNERS if spec.name == family)


def _project(repo_root: Path, family: str, role: str) -> None:
    root, suffix = roles.AGENT_ROOTS[family]
    target = repo_root / root / f"{role}{suffix}"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("---\nname: x\n---\n", encoding="utf-8")


def test_every_mapped_role_is_a_role_this_catalog_actually_ships() -> None:
    """The map cannot name a persona nobody authored.

    Run against the real catalog, which is the only place it means anything: a
    table pointing at a role that does not exist puts a flag on the argv the host
    drops without a word, which is the failure mode the resolution exists to stop.
    """
    authored = {path.parent.name for path in Path(".basicly/core/agents").glob("*/agent.yaml")}

    mapped = set(roles.ROLE_BY_PHASE.values()) | set(roles.LENS_ROLE_BY_PHASE.values())

    unknown = sorted(mapped - authored)

    assert unknown == [], f"phase map names role(s) with no source: {unknown}"


def test_every_loop_role_is_reachable_from_one_of_the_phase_tables() -> None:
    """basicly-feje's AC: an authored loop role nothing routes to is context with no reach.

    The loop-role set is §6.2's, spelled out rather than derived, because nothing in an
    agent source declares whether it is engine-dispatched or ad-hoc — and an ad-hoc agent
    (`architect`, `code-reviewer`, `researcher`, `security-auditor`, `test-runner`) is
    invoked by a human and must not appear in a phase table at all.

    `reviewer` is why this exists: it was authored, projected to both agent roots and
    vendored to consumers while `ROLE_BY_PHASE` was phase-to-one-role, so no phase string
    could ever resolve it.
    """
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
    """§3.1 gives VALIDATE `validator` **and** `reviewer` by lens; both must be reachable.

    The lens is what makes the second dispatchable more than once: a phase-to-one-role map
    can express neither, which is the shape defect basicly-feje was filed on.
    """
    assert roles.role_for_phase("validate") == "validator"

    reviews = roles.lens_dispatches("validate")

    assert [dispatch.role for dispatch in reviews] == ["reviewer"] * len(roles.REVIEW_LENSES)
    assert [dispatch.lens for dispatch in reviews] == list(roles.REVIEW_LENSES)
    assert len(roles.REVIEW_LENSES) == len(set(roles.REVIEW_LENSES))


def test_no_other_phase_pays_for_a_lens_dispatch() -> None:
    """Every lens is a paid dispatch, so a phase that fans out over nothing costs nothing.

    BUILD and REPAIR are where a lane's budget actually goes and VERIFY is deterministic
    gates by decision — a review dispatched at any of them is spend with no state that
    consumes it.
    """
    fanned_out = [phase for phase in roles.ROLE_BY_PHASE if roles.lens_dispatches(phase)]

    assert fanned_out == ["validate"]
    assert roles.lens_dispatches("verify") == ()


def test_a_named_role_resolves_only_when_its_family_can_load_it(tmp_path: Path) -> None:
    """The fan-out uses the explicit resolver, so it inherits the same two refusals.

    A reviewer whose projected file is absent falls back to the default runner rather than
    putting a flag on the argv the host drops without a word.
    """
    assert roles.resolve_named_role(tmp_path, _spec("claude"), "reviewer") is None

    _project(tmp_path, "claude", "reviewer")

    assert roles.resolve_named_role(tmp_path, _spec("claude"), "reviewer") == "reviewer"
    assert roles.resolve_named_role(tmp_path, _spec("codex"), "reviewer") is None


def test_a_phase_with_no_persona_resolves_to_nothing(tmp_path: Path) -> None:
    """None is an answer, not an error: VERIFY is deterministic gates by decision."""
    assert roles.role_for_phase("verify") is None
    assert roles.role_for_phase("done") is None
    assert roles.resolve_role(tmp_path, _spec("claude"), "verify") is None


def test_repair_resolves_to_the_implementer_because_it_is_a_mode() -> None:
    """D5 admits a persona on tier, tools or artifact; repair differs in none."""
    assert roles.role_for_phase("repair") == roles.role_for_phase("build") == "implementer"


def test_an_unprojected_role_resolves_to_nothing_rather_than_to_its_name(
    tmp_path: Path,
) -> None:
    """A consumer who has not upgraded gets an unspecialised loop, not a stopped one.

    Checked against the projected file rather than the source, because the
    projected file is what the host reads.
    """
    assert roles.resolve_role(tmp_path, _spec("claude"), "build") is None

    _project(tmp_path, "claude", "implementer")

    assert roles.resolve_role(tmp_path, _spec("claude"), "build") == "implementer"


def test_a_family_with_no_subagent_root_never_resolves_a_role(tmp_path: Path) -> None:
    """Codex ships none, so the parity gap is declared rather than discovered."""
    _project(tmp_path, "claude", "implementer")

    assert roles.resolve_role(tmp_path, _spec("codex"), "build") is None


@pytest.mark.parametrize("family", ["claude", "copilot"])
def test_a_resolved_role_reaches_the_argv(family: str) -> None:
    """The whole point: the engine names a role and the host is told to load it."""
    spec = next(spec for spec in BUILTIN_RUNNERS if spec.name == family)

    argv = format_command(spec, "PROMPT", role="implementer")

    assert argv[1:3] == ["--agent", "implementer"]
    assert "PROMPT" in argv


def test_codex_argv_is_unchanged_by_a_role_it_cannot_select() -> None:
    """A routing flag lost is an unspecialised dispatch; it must not raise.

    Deliberately unlike deny_tools, which raises when it cannot be spelled: a
    denial silently dropped is a guarantee silently dropped, and this is not that.
    """
    spec = next(spec for spec in BUILTIN_RUNNERS if spec.name == "codex")

    assert format_command(spec, "PROMPT", role="implementer") == format_command(spec, "PROMPT")


def test_no_role_leaves_every_family_exactly_as_it_was() -> None:
    """The default-runner path must be byte-identical, or this change is not additive."""
    for spec in BUILTIN_RUNNERS:
        if spec.command:
            assert format_command(spec, "PROMPT", role=None) == format_command(spec, "PROMPT")
