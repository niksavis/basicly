from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from basicly import loop_state, policy, tracker
from basicly.config import VERIFY_GATE_PROVIDER, PolicyConfig
from basicly.loop_state import WorktreeBinding
from basicly.policy import GateStatus
from tests import fake_tracker

CONFIG = PolicyConfig(required_gates=("verify",), max_rework=2)

REPO_ROOT = Path(__file__).parent.parent
KIT_SOURCE = REPO_ROOT / tracker.KIT_TRACKER_DIR

CLOCK = 1_000_000_000.0


class _Proc:
    def __init__(self, stdout: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


class _FakeBr:
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
    monkeypatch.setattr(policy, "_write", fake)
    fake_tracker.install(monkeypatch, fake)
    fake_tracker.install_graph(monkeypatch, fake)


def _gate_status(*, can_advance: bool) -> GateStatus:
    if can_advance:
        return GateStatus(True, ("verify",), (), (), ())
    return GateStatus(False, (), (), ("verify",), ())


def test_worktree_ref_roundtrips() -> None:
    ref = loop_state.format_worktree_ref("loop-state", "harness/loop-state")
    assert loop_state.parse_worktree_ref(ref) == WorktreeBinding("loop-state", "harness/loop-state")


@pytest.mark.parametrize("ref", [None, "", "some-other-ref", "worktree:", "worktree:only"])
def test_worktree_ref_rejects_unset_or_foreign(ref: str | None) -> None:
    assert loop_state.parse_worktree_ref(ref) is None


@pytest.mark.parametrize("status", sorted(loop_state.DISPATCHABLE_STATUSES))
def test_every_dispatchable_status_is_admitted(status: str) -> None:
    assert loop_state.is_dispatchable(status) is True


@pytest.mark.parametrize("status", ["closed", "tombstone", "deferred"])
def test_a_terminal_or_parked_status_is_not_dispatchable(status: str) -> None:

    assert loop_state.is_dispatchable(status) is False


def test_the_named_sets_partition_the_known_vocabulary() -> None:

    refused = {"closed", "tombstone", "deferred"}
    assert loop_state.DISPATCHABLE_STATUSES | refused == loop_state.KNOWN_STATUSES
    assert not loop_state.DISPATCHABLE_STATUSES & refused


@pytest.mark.parametrize("status", ["rework", "in_review"])
def test_a_project_defined_status_is_admitted_rather_than_dropped(status: str) -> None:

    assert status not in loop_state.KNOWN_STATUSES
    assert loop_state.is_dispatchable(status) is True


_PHASE_CASES = [
    ("closed", ("ship",), None, True, True, "done"),
    ("in_progress", ("ship",), None, True, False, "ship"),
    ("in_progress", ("classify", "ship"), WorktreeBinding("n", "b"), False, False, "build"),
    ("in_progress", ("ship",), WorktreeBinding("n", "b"), True, False, "ship"),
    ("open", ("ship",), None, False, False, "intake"),
    ("in_progress", ("classify", "ship"), None, False, False, "classify"),
    ("in_progress", ("classify", "decompose", "ship"), None, False, False, "decompose"),
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
    status, checkpoints, worktree, advance, children, expected = case
    phase = loop_state.derive_phase(
        status, checkpoints, worktree, _gate_status(can_advance=advance), children
    )
    assert phase == expected


def test_read_node_state_folds_all_signals(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = _FakeBr(
        status="in_progress",
        external_ref=loop_state.format_worktree_ref("feat", "harness/feat"),
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
    assert state.phase == "verify"


def test_read_node_state_intake_when_nothing_recorded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install(monkeypatch, _FakeBr(status="open"))
    state = loop_state.read_node_state(tmp_path, "i", CONFIG)
    assert state.phase == "intake"
    assert state.worktree is None
    assert state.checkpoints == ()
    assert state.rework == {"verify": 0}


def test_read_node_state_decompose_phase_from_children(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install(
        monkeypatch,
        _FakeBr(dependents=[{"dependency_type": "parent-child", "id": "i.1"}]),
    )
    state = loop_state.read_node_state(tmp_path, "i", CONFIG)
    assert state.has_children is True
    assert state.phase == "decompose"


def test_loop_state_exposes_no_session_walk_of_its_own() -> None:

    assert not hasattr(loop_state, "session_issue_ids")


def test_ready_ranked_parses_scheduler(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
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

    _install(
        monkeypatch,
        _FakeBr(
            ready=[
                {"rank": 1, "fallback_rank": 3, "score": 49, "issue": {"id": "a", "title": "x"}},
            ],
            scheduler_envelope={
                "schema": "tracker.scheduler.v1",
                "fallback_policy": {"sort": "priority ASC, created_at ASC, id ASC"},
            },
        ),
    )
    ranking = loop_state.ready_ranking(tmp_path)

    assert ranking.schema == "tracker.scheduler.v1"
    assert ranking.fallback_sort == "priority ASC, created_at ASC, id ASC"
    assert ranking.nodes[0].rank == 1
    assert ranking.nodes[0].fallback_rank == 3
    assert ranking.by_issue()["a"].score == 49


def test_ready_ranking_degrades_without_the_envelope(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install(monkeypatch, _FakeBr(ready=[{"rank": 2, "score": 5, "issue": {"id": "a"}}]))
    ranking = loop_state.ready_ranking(tmp_path)

    assert ranking.schema == ""
    assert ranking.fallback_sort == ""
    assert ranking.nodes[0].fallback_rank == 2


def test_blocked_ids_parses_blocked_list(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install(monkeypatch, _FakeBr(blocked=[{"id": "x"}, {"id": "y"}]))
    assert loop_state.blocked_ids(tmp_path) == ("x", "y")


def _owned_repo(tmp_path: Path) -> Path:
    kit_dir = tmp_path / tracker.KIT_TRACKER_DIR
    kit_dir.mkdir(parents=True)
    for source in sorted(KIT_SOURCE.glob("*.py")):
        shutil.copy2(source, kit_dir / source.name)
    (tmp_path / "basicly.toml").write_text(
        f'[tracker]\nmode = "{tracker.MODE_OWNED}"\n', encoding="utf-8"
    )
    return tmp_path


def _seed_ledger(repo: Path) -> None:

    kit = tracker.kit(repo)
    events, edge = kit.events, kit.migrate
    kit.events.append(
        tracker.ledger_dir(repo),
        [
            events.Draft("rank-aa01", events.KIND_CREATED, {"title": "critical", "priority": 0}),
            events.Draft("rank-aa01", events.KIND_STATUS, {"status": "open"}),
            events.Draft("rank-bb02", events.KIND_CREATED, {"title": "waiting", "priority": 0}),
            events.Draft("rank-bb02", events.KIND_STATUS, {"status": "open"}),
            events.Draft(
                "rank-bb02",
                edge.KIND_EDGE,
                {edge.EDGE_TO: "rank-aa01", edge.EDGE_TYPE: "blocks"},
            ),
            events.Draft("rank-cc03", events.KIND_CREATED, {"title": "ordinary", "priority": 2}),
            events.Draft("rank-cc03", events.KIND_STATUS, {"status": "open"}),
        ],
        clock=lambda: CLOCK,
    )


def test_ready_ranking_reads_the_owned_scorer_after_the_flip(tmp_path: Path) -> None:

    repo = _owned_repo(tmp_path)
    _seed_ledger(repo)
    scheduler = tracker.kit(repo, tracker.SCHEDULER_KIT_MODULE)

    ranking = loop_state.ready_ranking(repo)

    assert ranking.schema == scheduler.SCHEMA
    assert ranking.fallback_sort == scheduler.SORT
    assert [node.issue_id for node in ranking.nodes] == ["rank-aa01", "rank-cc03"]
    assert [node.title for node in ranking.nodes] == ["critical", "ordinary"]


def test_a_ranking_from_the_owned_scorer_stays_explainable(tmp_path: Path) -> None:

    repo = _owned_repo(tmp_path)
    _seed_ledger(repo)
    scheduler = tracker.kit(repo, tracker.SCHEDULER_KIT_MODULE)

    leader = loop_state.ready_ranking(repo).nodes[0]

    assert scheduler.explain(leader.score) == scheduler.ScoreTerms(priority=0, dependents=1)
    assert leader.fallback_rank == leader.rank == 1


def test_the_owned_ranking_honours_a_limit(tmp_path: Path) -> None:
    repo = _owned_repo(tmp_path)
    _seed_ledger(repo)

    assert [node.issue_id for node in loop_state.ready_ranked(repo, limit=1)] == ["rank-aa01"]


def test_a_flipped_repo_without_the_kit_stops_rather_than_reading_as_no_work(
    tmp_path: Path,
) -> None:

    (tmp_path / "basicly.toml").write_text(
        f'[tracker]\nmode = "{tracker.MODE_OWNED}"\n', encoding="utf-8"
    )
    with pytest.raises(tracker.TrackerDivergenceError, match="not installed"):
        loop_state.ready_ranking(tmp_path)
