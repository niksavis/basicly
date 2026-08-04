"""Tests for resumable loop-state reconstruction (onb.6.1)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from basicly import loop_state, policy
from basicly.config import VERIFY_GATE_PROVIDER, PolicyConfig
from basicly.loop_state import WorktreeBinding
from basicly.policy import GateStatus

CONFIG = PolicyConfig(required_gates=("verify",), max_rework=2)


class _Proc:
    def __init__(self, stdout: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


class _FakeBr:
    """Stateful stand-in for br, routed by subcommand.

    Serves one issue record plus its gate list and comments, so read_node_state
    (which delegates gate/checkpoint/rework reads to the policy engine) resolves
    entirely against this fake when installed on both modules.
    """

    def __init__(
        self,
        *,
        gates: list[dict] | None = None,
        comments: list[str] | None = None,
        ready: list[dict] | None = None,
        blocked: list[dict] | None = None,
        scheduler_envelope: dict | None = None,
        **record: object,
    ) -> None:
        # Any issue field can be overridden by keyword (status, external_ref,
        # agent_context, dependents, ...); an absent agent_context stays absent.
        self.record: dict = {
            "id": "i",
            "status": "in_progress",
            "issue_type": "task",
            "external_ref": None,
            "dependents": [],
        }
        self.record.update(record)
        self.gates = gates or []
        self.comments = comments or []
        self.ready = ready or []
        self.blocked = blocked or []
        # br wraps its recommendations in a versioned envelope; a test that needs
        # the policy fields supplies them, and omitting them exercises the
        # degradation path against an older br.
        self.scheduler_envelope = scheduler_envelope or {}

    def __call__(self, _repo_root: Path, args: list[str], *, _check: bool = True) -> _Proc:
        if args[:1] == ["show"]:
            return _Proc(json.dumps([self.record]))
        if args[:2] == ["gate", "list"]:
            return _Proc(json.dumps({"results": self.gates}))
        if args[:2] == ["comments", "list"]:
            return _Proc(json.dumps([{"text": t} for t in self.comments]))
        if args[:1] == ["scheduler"]:
            return _Proc(json.dumps({**self.scheduler_envelope, "recommendations": self.ready}))
        if args[:1] == ["blocked"]:
            return _Proc(json.dumps(self.blocked))
        raise AssertionError(f"unexpected br call: {args}")


def _install(monkeypatch: pytest.MonkeyPatch, fake: _FakeBr) -> None:
    monkeypatch.setattr(loop_state, "_run_br", fake)
    monkeypatch.setattr(policy, "_run_br", fake)


def _gate_status(*, can_advance: bool) -> GateStatus:
    if can_advance:
        return GateStatus(True, ("verify",), (), (), ())
    return GateStatus(False, (), (), ("verify",), ())


# --- Worktree binding schema ------------------------------------------------


def test_worktree_ref_roundtrips() -> None:
    """A binding formatted onto external_ref parses back identically."""
    ref = loop_state.format_worktree_ref("loop-state", "harness/loop-state")
    assert loop_state.parse_worktree_ref(ref) == WorktreeBinding("loop-state", "harness/loop-state")


@pytest.mark.parametrize("ref", [None, "", "some-other-ref", "worktree:", "worktree:only"])
def test_worktree_ref_rejects_unset_or_foreign(ref: str | None) -> None:
    """An unset, foreign, or malformed external_ref yields no binding."""
    assert loop_state.parse_worktree_ref(ref) is None


# --- Dispatch candidacy (basicly-toj6) --------------------------------------


@pytest.mark.parametrize("status", sorted(loop_state.DISPATCHABLE_STATUSES))
def test_every_dispatchable_status_is_admitted(status: str) -> None:
    """The rule admits exactly the set it names, so the constant is not decoration."""
    assert loop_state.is_dispatchable(status) is True


@pytest.mark.parametrize("status", ["closed", "tombstone", "deferred"])
def test_a_terminal_or_parked_status_is_not_dispatchable(status: str) -> None:
    """The named refusals: the work is over, or a human parked it (basicly-toj6).

    ``deferred`` is the one this bead is about. The rule it replaced read
    ``status != "closed"``, so deferring a bead removed it from nothing: it stayed
    a sizing, funding and dispatch candidate, and it held its parent open.
    """
    assert loop_state.is_dispatchable(status) is False


def test_the_named_sets_partition_the_known_vocabulary() -> None:
    """Every status ``br schema`` declares is decided by name, none by omission.

    This is what stops the rule from drifting the way its predecessor did: a
    status added to :data:`loop_state.KNOWN_STATUSES` without a decision here
    fails, instead of silently landing on whichever side the code happened to
    default to.
    """
    refused = {"closed", "tombstone", "deferred"}
    assert loop_state.DISPATCHABLE_STATUSES | refused == loop_state.KNOWN_STATUSES
    assert not loop_state.DISPATCHABLE_STATUSES & refused


@pytest.mark.parametrize("status", ["rework", "in_review"])
def test_a_project_defined_status_is_admitted_rather_than_dropped(status: str) -> None:
    """A project may define its own statuses, so an unknown one must not be defunded.

    ``workflow.status_groups.ready`` in ``.beads/policy.yaml`` can widen readiness
    to a status this vocabulary has never heard of (br's own example is
    ``rework``). Refusing it would be the mirror of the bug being fixed: the child
    would be left out of the band table *and* its parent would fan in over it.
    """
    assert status not in loop_state.KNOWN_STATUSES
    assert loop_state.is_dispatchable(status) is True


# --- Phase derivation -------------------------------------------------------


# Each case orders the derive_phase inputs then the expected phase:
# status, checkpoints, worktree, can-advance, has-children, then expected.
_PHASE_CASES = [
    ("closed", ("ship",), None, True, True, "done"),
    # Torn down after a proven merge: no binding, but the landing left the
    # required gate green. This is the case the never-built leaf below is
    # indistinguishable from unless the gate is consulted.
    ("in_progress", ("ship",), None, True, False, "ship"),
    # Ship approved but the node has not landed: the worktree is still bound and
    # its verify gate is not green (e.g. the build->verify landing failed on a
    # transient lock). Must derive as build so the next advance re-lands, not
    # wedge at ship (basicly-k35r).
    ("in_progress", ("classify", "ship"), WorktreeBinding("n", "b"), False, False, "build"),
    # Ship approved and verify green on a still-bound worktree: merged, pending
    # teardown — legitimately ship.
    ("in_progress", ("ship",), WorktreeBinding("n", "b"), True, False, "ship"),
    # Ship approved out of order on a node that never built (basicly-jr0l.49).
    # It has no binding either, so `worktree is None` alone read as "torn down
    # after the merge" and derived ship, and the advance then closed the bead
    # with zero work done. Landed evidence is the green required gate, which
    # only the build->verify landing records — so each of these derives the
    # phase its own recorded evidence actually supports, never ship.
    ("open", ("ship",), None, False, False, "intake"),
    ("in_progress", ("classify", "ship"), None, False, False, "classify"),
    ("in_progress", ("classify", "decompose", "ship"), None, False, False, "decompose"),
    # Same hole one rung up: a feature whose children exist but whose own gate is
    # not green has not landed either.
    ("in_progress", ("ship",), None, False, True, "decompose"),
    ("in_progress", (), WorktreeBinding("n", "b"), True, False, "verify"),
    ("in_progress", (), WorktreeBinding("n", "b"), False, False, "build"),
    ("in_progress", ("decompose",), None, False, False, "decompose"),
    ("in_progress", (), None, False, True, "decompose"),
    ("in_progress", ("classify",), None, False, False, "classify"),
    ("open", (), None, False, False, "intake"),
]


@pytest.mark.parametrize("case", _PHASE_CASES)
def test_derive_phase_ladder(case: tuple) -> None:
    """Each recorded-evidence combination maps to the furthest reached phase."""
    status, checkpoints, worktree, advance, children, expected = case
    phase = loop_state.derive_phase(
        status, checkpoints, worktree, _gate_status(can_advance=advance), children
    )
    assert phase == expected


# --- Node state reconstruction ----------------------------------------------


def test_read_node_state_folds_all_signals(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A bound, green, checkpointed issue reconstructs into a verify-phase state."""
    fake = _FakeBr(
        status="in_progress",
        external_ref=loop_state.format_worktree_ref("feat", "harness/feat"),
        # The engine's own provider — a required gate no longer counts a foreign
        # one (basicly-jr0l.51).
        gates=[{"gate": "verify", "provider": VERIFY_GATE_PROVIDER, "passed": True}],
        comments=[
            "[harness-policy] checkpoint=classify approved",
            "[harness-policy] rework gate=verify",
        ],
    )
    _install(monkeypatch, fake)

    state = loop_state.read_node_state(tmp_path, "i", CONFIG)

    assert state.worktree == WorktreeBinding("feat", "harness/feat")
    assert state.gates.can_advance is True
    assert state.checkpoints == ("classify",)
    assert state.rework == {"verify": 1}
    assert state.agent_context is None  # absent in br => graceful None
    assert state.phase == "verify"


def test_read_node_state_intake_when_nothing_recorded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A fresh issue with no binding, gates, or checkpoints reads as intake."""
    _install(monkeypatch, _FakeBr(status="open"))
    state = loop_state.read_node_state(tmp_path, "i", CONFIG)
    assert state.phase == "intake"
    assert state.worktree is None
    assert state.checkpoints == ()
    assert state.rework == {"verify": 0}


def test_read_node_state_surfaces_agent_context_when_present(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Inherited agent_context is surfaced verbatim when the tracker records it."""
    _install(monkeypatch, _FakeBr(agent_context='{"design": "keep it thin"}'))
    state = loop_state.read_node_state(tmp_path, "i", CONFIG)
    assert state.agent_context == '{"design": "keep it thin"}'


def test_read_node_state_decompose_phase_from_children(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An issue with a parent-child dependent counts as decomposed."""
    _install(
        monkeypatch,
        _FakeBr(dependents=[{"dependency_type": "parent-child", "id": "i.1"}]),
    )
    state = loop_state.read_node_state(tmp_path, "i", CONFIG)
    assert state.has_children is True
    assert state.phase == "decompose"


# --- The one session walk (basicly-tcmy.28) ---------------------------------


def test_loop_state_exposes_no_session_walk_of_its_own() -> None:
    """There is one session walk, and it is ``policy.session_issue_ids``.

    This module used to carry a second one that followed parent-child dependents
    only. It disagreed with the real walk by 14 beads on ``basicly-kjc5``, and the
    decision queue read the narrow one — so a delegated answer on a blocks-reachable
    bead never counted against ``decider_max_decisions`` and an escalation on one was
    invisible to loop status. basicly-jr0l.40 fixed the copy in ``policy`` and left
    this one live, so the guard is against the *name* coming back: a re-export here
    is how a consumer starts reading a walk that is nobody's job to keep correct.
    """
    assert not hasattr(loop_state, "session_issue_ids")


# --- Ready / blocked sets ---------------------------------------------------


def test_ready_ranked_parses_scheduler(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """ready_ranked maps scheduler recommendations to ranked nodes in order."""
    _install(
        monkeypatch,
        _FakeBr(
            ready=[
                {"rank": 1, "score": 49, "issue": {"id": "a", "title": "first"}},
                {"rank": 2, "score": 30, "issue": {"id": "b", "title": "second"}},
            ]
        ),
    )
    ranked = loop_state.ready_ranked(tmp_path)
    assert [(n.rank, n.score, n.issue_id, n.title) for n in ranked] == [
        (1, 49, "a", "first"),
        (2, 30, "b", "second"),
    ]


def test_ready_ranking_captures_the_policy_envelope(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A rank is uninterpretable without the policy that produced it (basicly-vkh0.3).

    D9 wants dispatch inputs reproducible, and the score is an integer on a scale
    br owns — so the schema version and the tie-break sort are part of the answer,
    not decoration.
    """
    _install(
        monkeypatch,
        _FakeBr(
            ready=[
                {"rank": 1, "fallback_rank": 3, "score": 49, "issue": {"id": "a", "title": "x"}},
            ],
            scheduler_envelope={
                "schema": "br.scheduler.v1",
                "fallback_policy": {"sort": "priority ASC, created_at ASC, id ASC"},
            },
        ),
    )
    ranking = loop_state.ready_ranking(tmp_path)

    assert ranking.schema == "br.scheduler.v1"
    assert ranking.fallback_sort == "priority ASC, created_at ASC, id ASC"
    # Evidence weighting moved this node from 3rd to 1st; recording only the final
    # rank would hide that the score is what did it.
    assert ranking.nodes[0].rank == 1
    assert ranking.nodes[0].fallback_rank == 3
    assert ranking.by_issue()["a"].score == 49


def test_ready_ranking_degrades_without_the_envelope(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An older br emits no schema or fallback_rank; the ranks must still parse."""
    _install(monkeypatch, _FakeBr(ready=[{"rank": 2, "score": 5, "issue": {"id": "a"}}]))
    ranking = loop_state.ready_ranking(tmp_path)

    assert ranking.schema == ""
    assert ranking.fallback_sort == ""
    # No fallback_rank reported: br's documented behaviour is to preserve the rank.
    assert ranking.nodes[0].fallback_rank == 2


def test_blocked_ids_parses_blocked_list(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """blocked_ids returns just the ids of blocked issues."""
    _install(monkeypatch, _FakeBr(blocked=[{"id": "x"}, {"id": "y"}]))
    assert loop_state.blocked_ids(tmp_path) == ("x", "y")
