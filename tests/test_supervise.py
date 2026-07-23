"""Tests for the supervisor: lock, session, recovery, and concurrent dispatch.

Part 1 (basicly-kjc5.5) pins the three lock/session properties: exactly one
supervisor may own a repo, a crashed holder's lock is taken over atomically by
exactly one contender, and a restart re-derives the whole session from ``br``.
Part 2 (basicly-kjc5.6) pins the dispatch layer: bundles are pure functions of
``br`` state at dispatch time with found-info folding, lanes fan out
concurrently up to the cap under a heartbeating lock, and the usage meter
spins an idempotent follow-up bead when a run crosses the context ceiling.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import pytest

from basicly import loop, loop_state, needs_input, policy, runner, supervise
from basicly.config import PolicyConfig, SizingConfig
from basicly.supervise import LOCK_FILE, STALE_AFTER_S, LockHeldError, LockLostError


class _Proc:
    def __init__(self, stdout: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


def _lock_path(repo: Path) -> Path:
    return repo / LOCK_FILE


def _backdate(path: Path, seconds: float) -> None:
    stat = path.stat()
    os.utime(path, (stat.st_atime - seconds, stat.st_mtime - seconds))


# --- Lock: acquire / refuse / heartbeat --------------------------------------


def test_acquire_creates_lock_with_session_payload(tmp_path: Path) -> None:
    """The lock lands in the self-ignored usage dir carrying pid + session + root."""
    lock = supervise.acquire(tmp_path, "epic:abcd1234", "epic")
    payload = json.loads(lock.read_text(encoding="utf-8"))
    assert payload["pid"] == os.getpid()
    assert payload["session_id"] == "epic:abcd1234"
    assert payload["root_issue"] == "epic"
    assert (tmp_path / ".basicly/usage/.gitignore").read_text(encoding="utf-8") == "*\n"


def test_second_acquire_refuses_while_first_heartbeats(tmp_path: Path) -> None:
    """A fresh heartbeat refuses the contender and names the holder."""
    supervise.acquire(tmp_path, "epic:first", "epic")
    with pytest.raises(LockHeldError, match="epic:first"):
        supervise.acquire(tmp_path, "epic:second", "epic")


def test_heartbeat_keeps_an_aging_lock_fresh(tmp_path: Path) -> None:
    """Heartbeating refreshes the mtime, so a live holder is never stolen from."""
    lock = supervise.acquire(tmp_path, "epic:first", "epic")
    _backdate(lock, STALE_AFTER_S + 5)
    supervise.heartbeat(lock, "epic:first")  # the holder beats just in time
    with pytest.raises(LockHeldError, match="epic:first"):
        supervise.acquire(tmp_path, "epic:second", "epic")


def test_heartbeat_raises_lock_lost_when_lock_vanished(tmp_path: Path) -> None:
    """A vanished lock tells the stalled holder to stop supervising."""
    lock = supervise.acquire(tmp_path, "epic:first", "epic")
    lock.unlink()
    with pytest.raises(LockLostError):
        supervise.heartbeat(lock, "epic:first")


def test_stalled_holder_heartbeat_fences_after_takeover(tmp_path: Path) -> None:
    """A resumed stalled holder must stand down, not refresh the successor's lock.

    The real interleaving: stall past stale, takeover completes, and the old
    holder's next beat finds the successor's lock at the same path.
    """
    lock = supervise.acquire(tmp_path, "epic:first", "epic")
    _backdate(lock, STALE_AFTER_S + 1)  # the holder stalls past the horizon
    supervise.acquire(tmp_path, "epic:successor", "epic")  # takeover completes
    with pytest.raises(LockLostError, match="successor"):
        supervise.heartbeat(lock, "epic:first")


# --- Lock: stale takeover -----------------------------------------------------


def test_stale_lock_is_taken_over_atomically(tmp_path: Path) -> None:
    """A lock past the staleness horizon is stolen; the new payload owns it."""
    lock = supervise.acquire(tmp_path, "epic:crashed", "epic")
    _backdate(lock, STALE_AFTER_S + 1)
    took = supervise.acquire(tmp_path, "epic:successor", "epic")
    payload = json.loads(took.read_text(encoding="utf-8"))
    assert payload["session_id"] == "epic:successor"
    assert not list(lock.parent.glob("*.stale.*"))  # tombstone cleaned up


def test_takeover_loser_gets_lock_held(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The contender that loses the atomic rename refuses instead of double-owning."""
    lock = supervise.acquire(tmp_path, "epic:crashed", "epic")
    _backdate(lock, STALE_AFTER_S + 1)

    def losing_replace(_self: object, _dst: object) -> None:
        raise FileNotFoundError  # the other contender renamed it first

    monkeypatch.setattr(supervise.Path, "replace", losing_replace)
    with pytest.raises(LockHeldError, match="taking over"):
        supervise.acquire(tmp_path, "epic:loser", "epic")


def test_acquire_retries_when_lock_freed_mid_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A lock released between the failed create and the holder read is free."""
    lock = supervise.acquire(tmp_path, "epic:first", "epic")

    real_read_holder = supervise.read_holder

    def freeing_read_holder(repo_root: Path) -> object:
        lock.unlink(missing_ok=True)  # the holder releases just now
        return real_read_holder(repo_root)

    monkeypatch.setattr(supervise, "read_holder", freeing_read_holder)
    took = supervise.acquire(tmp_path, "epic:second", "epic")
    payload = json.loads(took.read_text(encoding="utf-8"))
    assert payload["session_id"] == "epic:second"


def test_takeover_replaces_an_abandoned_same_pid_tombstone(tmp_path: Path) -> None:
    """A tombstone leaked by a crashed same-pid takeover never blocks the next one."""
    lock = supervise.acquire(tmp_path, "epic:crashed", "epic")
    _backdate(lock, STALE_AFTER_S + 1)
    leaked = lock.with_name(f"{lock.name}.stale.{os.getpid()}")
    leaked.write_text("{}", encoding="utf-8")
    took = supervise.acquire(tmp_path, "epic:successor", "epic")
    assert json.loads(took.read_text(encoding="utf-8"))["session_id"] == "epic:successor"


def test_corrupt_fresh_lock_still_refuses(tmp_path: Path) -> None:
    """Staleness is mtime-only: an unreadable but fresh lock is not stolen."""
    path = _lock_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("not json", encoding="utf-8")
    with pytest.raises(LockHeldError, match="unknown"):
        supervise.acquire(tmp_path, "epic:second", "epic")


# --- Lock: release ------------------------------------------------------------


def test_release_removes_only_own_lock(tmp_path: Path) -> None:
    """Release is content-checked so a stalled holder never deletes a successor's lock."""
    lock = supervise.acquire(tmp_path, "epic:first", "epic")
    supervise.release(lock, "epic:someone-else")
    assert lock.exists()  # not ours to delete
    supervise.release(lock, "epic:first")
    assert not lock.exists()
    supervise.release(lock, "epic:first")  # idempotent on a missing lock


# --- Session derivation (crash recovery = re-reading br) ----------------------


class _FakeBrShow:
    """br stand-in serving `show --json` from a seeded issue map."""

    def __init__(self, issues: dict[str, dict]) -> None:
        self.issues = issues

    def __call__(self, _repo_root: Path, args: list[str], *, _check: bool = True) -> _Proc:
        if args[:1] == ["show"]:
            return _Proc(json.dumps([self.issues[args[1]]]))
        raise AssertionError(f"unexpected br call: {args}")


def _issue(
    issue_id: str,
    status: str = "open",
    external_ref: str | None = None,
    children: tuple[tuple[str, str], ...] = (),
) -> dict:
    return {
        "id": issue_id,
        "status": status,
        "external_ref": external_ref,
        "dependents": [
            {"id": cid, "status": cstatus, "dependency_type": "parent-child"}
            for cid, cstatus in children
        ],
    }


def _fake_sessions(monkeypatch: pytest.MonkeyPatch, names: set[str]) -> None:
    class _S:
        def __init__(self, name: str) -> None:
            self.name = name

    monkeypatch.setattr(
        supervise.worktree, "list_sessions", lambda *_a, **_k: [_S(n) for n in names]
    )


def test_derive_session_readopts_bound_open_children(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Open children with a worktree external_ref are re-adopted purely from br."""
    issues = {
        "epic": _issue("epic", children=(("epic.1", "in_progress"), ("epic.2", "open"))),
        "epic.1": _issue("epic.1", "in_progress", external_ref="worktree:epic-1:harness/epic-1"),
        "epic.2": _issue("epic.2", "open"),
    }
    monkeypatch.setattr(supervise, "_run_br", _FakeBrShow(issues))
    _fake_sessions(monkeypatch, {"epic-1"})

    state = supervise.derive_session(tmp_path, "epic")

    assert state.root_status == "open"
    assert state.open_children == ("epic.1", "epic.2")
    assert len(state.adopted) == 1
    lane = state.adopted[0]
    assert lane.issue_id == "epic.1"
    assert lane.binding == loop_state.WorktreeBinding("epic-1", "harness/epic-1")
    assert lane.live is True
    assert state.done is False


def test_derive_session_flags_missing_worktree_and_skips_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A bound lane whose worktree is gone is adopted dead; closed children never adopt."""
    issues = {
        "epic": _issue("epic", children=(("epic.1", "in_progress"), ("epic.2", "closed"))),
        "epic.1": _issue("epic.1", "in_progress", external_ref="worktree:epic-1:harness/epic-1"),
    }
    monkeypatch.setattr(supervise, "_run_br", _FakeBrShow(issues))
    _fake_sessions(monkeypatch, set())

    state = supervise.derive_session(tmp_path, "epic")

    assert [lane.issue_id for lane in state.adopted] == ["epic.1"]
    assert state.adopted[0].live is False
    assert state.open_children == ("epic.1",)


def test_derive_session_adopts_leaf_root_binding(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A leaf session (root itself bound to a worktree, no children) re-adopts the root."""
    issues = {
        "task": _issue("task", "in_progress", external_ref="worktree:task:harness/task"),
    }
    monkeypatch.setattr(supervise, "_run_br", _FakeBrShow(issues))
    _fake_sessions(monkeypatch, {"task"})

    state = supervise.derive_session(tmp_path, "task")

    assert [lane.issue_id for lane in state.adopted] == ["task"]
    assert state.children == ()
    assert state.done is False


def test_derive_session_done_when_all_children_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The session lifetime rule: everything closed (or root closed) means done."""
    issues = {
        "epic": _issue("epic", children=(("epic.1", "closed"), ("epic.2", "closed"))),
        "closed-root": _issue("closed-root", "closed"),
    }
    monkeypatch.setattr(supervise, "_run_br", _FakeBrShow(issues))
    _fake_sessions(monkeypatch, set())

    assert supervise.derive_session(tmp_path, "epic").done is True
    assert supervise.derive_session(tmp_path, "closed-root").done is True


def test_new_session_id_binds_root_and_varies() -> None:
    """Session ids carry the root issue and differ per start."""
    first = supervise.new_session_id("epic")
    second = supervise.new_session_id("epic")
    assert first.startswith("epic:")
    assert first != second


# --- Found-info records (basicly-kjc5.6, design 7.4/D6) ------------------------


class _FakeBr:
    """br stand-in serving show/comments/create/dep from seeded state."""

    def __init__(self, issues: dict[str, dict], comments: dict[str, list[str]] | None = None):
        self.issues = issues
        self.comments: dict[str, list[str]] = comments or {}
        self.created: list[list[str]] = []
        self.deps: list[tuple[str, ...]] = []
        self._next_id = 0

    def __call__(self, _repo_root: Path, args: list[str], *, _check: bool = True) -> _Proc:
        if args[:1] == ["show"]:
            return _Proc(json.dumps([self.issues[args[1]]]))
        if args[:2] == ["comments", "list"]:
            texts = self.comments.get(args[2], [])
            return _Proc(json.dumps([{"text": text} for text in texts]))
        if args[:2] == ["comments", "add"]:
            self.comments.setdefault(args[2], []).append(args[3])
            return _Proc("{}")
        if args[:1] == ["create"]:
            self._next_id += 1
            self.created.append(args)
            return _Proc(json.dumps({"id": f"new-{self._next_id}"}))
        if args[:2] == ["dep", "add"]:
            self.deps.append(tuple(args[2:]))
            return _Proc("{}")
        raise AssertionError(f"unexpected br call: {args}")


def test_parse_found_info_round_trips_the_marker() -> None:
    """A marker comment written by record_found_info parses back identically."""
    br = _FakeBr({})
    info = supervise.FoundInfo(
        kind="coupling",
        summary="config loader also reads runner windows",
        detail="split touched both",
        affects=("src/basicly/config.py", "epic.2"),
    )
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(supervise, "_run_br", br)
        supervise.record_found_info(Path(), "epic.1", info)
        records = supervise.found_info_records(Path(), ["epic.1"])
    assert records == (
        supervise.FoundInfo(
            kind="coupling",
            summary="config loader also reads runner windows",
            detail="split touched both",
            affects=("src/basicly/config.py", "epic.2"),
            source="epic.1",
        ),
    )


def test_record_found_info_rejects_unknown_kind() -> None:
    """The vocabulary is closed (design 7.4); a typo must not silently vanish."""
    with pytest.raises(ValueError, match="unknown found-info kind"):
        supervise.record_found_info(
            Path(), "epic.1", supervise.FoundInfo(kind="rumor", summary="s")
        )


def test_parse_found_info_skips_malformed_records() -> None:
    """Bad JSON, unknown kind, or an empty summary are advisory noise, never fatal."""
    assert supervise.parse_found_info("a plain comment", "x") is None
    assert supervise.parse_found_info("[harness-info] not json", "x") is None
    assert supervise.parse_found_info('[harness-info] {"kind":"rumor","summary":"s"}', "x") is None
    assert supervise.parse_found_info('[harness-info] {"kind":"fact","summary":" "}', "x") is None
    assert supervise.parse_found_info('[harness-info] ["not","object"]', "x") is None


# --- Dispatch bundles: pure functions of br state at dispatch time (D6) --------


def _bundle_issues() -> dict[str, dict]:
    return {
        "epic": _issue("epic", children=(("epic.1", "in_progress"), ("epic.2", "in_progress"))),
        "epic.1": {
            "id": "epic.1",
            "status": "in_progress",
            "description": "Do the work.\n\n## Scope\n\n- `src/a/**`\n",
        },
        "epic.2": _issue("epic.2", "in_progress"),
    }


def _fold_marker(kind: str, summary: str, affects: list[str]) -> str:
    payload = json.dumps({"kind": kind, "summary": summary, "detail": "", "affects": affects})
    return f"{supervise.INFO_MARKER} {payload}"


def test_build_bundle_folds_records_by_id_and_scope_overlap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Records naming the lane or overlapping its declared scope fold into the prompt."""
    br = _FakeBr(
        _bundle_issues(),
        comments={
            "epic": [_fold_marker("decision", "keep the loader split", ["epic.1"])],
            "epic.2": [
                _fold_marker("coupling", "core file is shared", ["src/a/core.py"]),
                _fold_marker("fact", "docs only", ["docs/**"]),
            ],
        },
    )
    monkeypatch.setattr(supervise, "_run_br", br)

    bundle = supervise.build_bundle(Path(), "epic.1", known_ids=frozenset({"epic", "epic.2"}))

    assert [info.summary for info in bundle.folded] == [
        "keep the loader split",
        "core file is shared",
    ]
    assert bundle.prompt.startswith(loop.dispatch_prompt("epic.1"))
    assert "keep the loader split" in bundle.prompt
    assert "docs only" not in bundle.prompt


def test_build_bundle_treats_session_bead_ids_as_ids_not_globs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A session-bead id in affects is never glob-tested.

    A broad scope like `**` must not false-fold records addressed to a
    different lane.
    """
    issues = _bundle_issues()
    issues["epic.1"]["description"] = "Broad.\n\n## Scope\n\n- `**`\n"
    br = _FakeBr(
        issues,
        comments={"epic": [_fold_marker("fact", "for the other lane", ["epic.2"])]},
    )
    monkeypatch.setattr(supervise, "_run_br", br)

    bundle = supervise.build_bundle(Path(), "epic.1", known_ids=frozenset({"epic", "epic.2"}))

    assert bundle.folded == ()


def test_build_bundle_sees_records_published_after_planning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Assembly reads br at call time (fresh at boundaries, never mid-flight — D6).

    A record landing between dispatches folds into the later bundle.
    """
    br = _FakeBr(_bundle_issues())
    monkeypatch.setattr(supervise, "_run_br", br)
    known = frozenset({"epic", "epic.2"})

    before = supervise.build_bundle(Path(), "epic.1", known_ids=known)
    br.comments["epic"] = [_fold_marker("constraint", "landed meanwhile", ["epic.1"])]
    after = supervise.build_bundle(Path(), "epic.1", known_ids=known)

    assert before.folded == ()
    assert [info.summary for info in after.folded] == ["landed meanwhile"]


# --- Usage meter: ceiling + finalize protocol (D8, design 7.6) -----------------


def _sizing(ceiling: float = 0.6) -> SizingConfig:
    return SizingConfig(
        working_set_min=8_000,
        working_set_max=64_000,
        build_factors={},
        calibration_min_samples=10,
        calibration_window=50,
        context_ceiling=ceiling,
    )


def test_ceiling_tokens_is_the_window_fraction() -> None:
    """The finalize trigger is context_ceiling of the runner's window."""
    claude = next(s for s in runner.BUILTIN_RUNNERS if s.name == "claude")
    assert supervise.ceiling_tokens(claude, _sizing(0.6)) == 120_000


def _overrun_issues() -> dict[str, dict]:
    return {
        "epic": _issue("epic", children=(("epic.1", "in_progress"),)),
        "epic.1": {
            "id": "epic.1",
            "status": "in_progress",
            "title": "Build the parser",
            "issue_type": "task",
            "acceptance_criteria": "- parses all three formats",
            "description": "Work.\n\n## Scope\n\n- `src/a/**`\n",
        },
    }


def test_finalize_followup_spins_a_gated_top_level_package(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The remainder becomes a sibling lane under the root (design 7.6).

    Gated on the overrun bead's landing, carrying the acceptance criteria
    and scope.
    """
    br = _FakeBr(_overrun_issues())
    monkeypatch.setattr(supervise, "_run_br", br)

    followup = supervise.finalize_followup(
        Path(), "epic", "epic.1", occupancy=130_000, ceiling=120_000
    )

    assert followup == "new-1"
    create = br.created[0]
    assert create[1] == "Follow-up: Build the parser (context-ceiling overrun)"
    parent_at = create.index("--parent")
    assert tuple(create[parent_at : parent_at + 2]) == ("--parent", "epic")
    body = create[create.index("-d") + 1]
    assert "- parses all three formats" in body
    assert "- `src/a/**`" in body
    assert ("new-1", "epic.1", "-t", "blocks") in br.deps
    marker = br.comments["epic.1"][-1]
    assert marker.startswith(supervise.OVERRUN_MARKER)
    assert "followup=new-1" in marker


def test_finalize_followup_is_idempotent_via_the_overrun_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A re-metered overrun returns the recorded follow-up instead of a duplicate."""
    br = _FakeBr(_overrun_issues())
    monkeypatch.setattr(supervise, "_run_br", br)

    first = supervise.finalize_followup(
        Path(), "epic", "epic.1", occupancy=130_000, ceiling=120_000
    )
    second = supervise.finalize_followup(
        Path(), "epic", "epic.1", occupancy=131_000, ceiling=120_000
    )

    assert first == second == "new-1"
    assert len(br.created) == 1


def test_finalize_followup_leaf_root_creates_without_parent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the overrun lane is the session root itself there is no parent to nest under."""
    issues = _overrun_issues()
    issues["epic.1"]["issue_type"] = "feature"  # non-leaf type falls back to task
    br = _FakeBr(issues)
    monkeypatch.setattr(supervise, "_run_br", br)

    supervise.finalize_followup(Path(), "epic.1", "epic.1", occupancy=1, ceiling=1)

    create = br.created[0]
    assert "--parent" not in create
    assert create[create.index("-t") + 1] == "task"


# --- Ready lanes and concurrent dispatch ---------------------------------------


def _lane(issue_id: str, live: bool = True) -> supervise.AdoptedLane:
    return supervise.AdoptedLane(
        issue_id=issue_id,
        status="in_progress",
        binding=loop_state.WorktreeBinding(issue_id, f"harness/{issue_id}"),
        live=live,
    )


def _session(*lanes: supervise.AdoptedLane) -> supervise.SessionState:
    return supervise.SessionState(
        root_issue="epic",
        root_status="open",
        children=tuple((lane.issue_id, lane.status) for lane in lanes),
        adopted=lanes,
    )


def _patch_readiness(
    monkeypatch: pytest.MonkeyPatch,
    *,
    blocked: frozenset[str] | set[str] = frozenset(),
    ranked: tuple[tuple[int, str], ...] = (),
) -> None:
    monkeypatch.setattr(supervise.loop_state, "blocked_ids", lambda _r: tuple(blocked))
    monkeypatch.setattr(
        supervise.loop_state,
        "ready_ranked",
        lambda _r: tuple(
            loop_state.RankedNode(rank=rank, score=0, issue_id=iid, title="")
            for rank, iid in ranked
        ),
    )
    monkeypatch.setattr(supervise.decisions, "has_pending", lambda _r, _i: False)
    monkeypatch.setattr(supervise, "_phase_of", lambda _r, _i: "build")


def test_ready_lanes_filters_blocked_and_dead_and_orders_by_rank(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Blocked-ness gates (claimed lanes leave the ready list); rank orders the rest."""
    lanes = (_lane("epic.1"), _lane("epic.2"), _lane("epic.3", live=False), _lane("epic.4"))
    _patch_readiness(monkeypatch, blocked={"epic.2"}, ranked=((1, "epic.4"),))

    ready = supervise.ready_lanes(Path(), _session(*lanes))

    assert [lane.issue_id for lane in ready] == ["epic.4", "epic.1"]


def test_dispatch_lanes_runs_concurrently_up_to_the_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Four ready lanes under cap 2 overlap two at a time, never more.

    The outcomes come back in dispatch (scheduler-rank) order.
    """
    lanes = tuple(_lane(f"epic.{n}") for n in (1, 2, 3, 4))
    _patch_readiness(monkeypatch, ranked=tuple((n, f"epic.{n}") for n in (1, 2, 3, 4)))
    monkeypatch.setattr(supervise.runner, "select_runner", lambda *_a, **_k: _MANUAL_SPEC)
    barrier = threading.Barrier(2, timeout=5)
    gauge = {"current": 0, "max": 0}
    gauge_lock = threading.Lock()

    def fake_dispatch(_repo, _session, lane, _spec, _sizing) -> supervise.LaneOutcome:
        with gauge_lock:
            gauge["current"] += 1
            gauge["max"] = max(gauge["max"], gauge["current"])
        barrier.wait()  # both slots must be occupied at once to pass
        with gauge_lock:
            gauge["current"] -= 1
        return _outcome(lane.issue_id)

    outcomes = supervise.dispatch_lanes(Path(), _session(*lanes), cap=2, dispatch_one=fake_dispatch)

    assert gauge["max"] == 2
    assert [o.issue_id for o in outcomes] == ["epic.1", "epic.2", "epic.3", "epic.4"]


def _outcome(issue_id: str) -> supervise.LaneOutcome:
    return supervise.LaneOutcome(
        issue_id=issue_id,
        runner_name="manual",
        result=None,
        needs_fact=None,
        occupancy=None,
        overrun=False,
        followup_id=None,
        detail="test",
    )


_MANUAL_SPEC = runner.RunnerSpec("manual", runner.HANDOFF)


def test_dispatch_lanes_heartbeats_while_runners_execute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The holder beats the lock between completions so a long pass never goes stale."""
    _patch_readiness(monkeypatch)
    monkeypatch.setattr(supervise.runner, "select_runner", lambda *_a, **_k: _MANUAL_SPEC)
    monkeypatch.setattr(supervise, "HEARTBEAT_INTERVAL_S", 0.01)
    release = threading.Event()
    beats = []

    def beat() -> None:
        beats.append(1)
        release.set()

    def fake_dispatch(_repo, _session, lane, _spec, _sizing) -> supervise.LaneOutcome:
        assert release.wait(timeout=5)
        return _outcome(lane.issue_id)

    outcomes = supervise.dispatch_lanes(
        Path(), _session(_lane("epic.1")), beat=beat, cap=1, dispatch_one=fake_dispatch
    )

    assert len(outcomes) == 1
    assert beats  # at least one beat fired while the lane ran


def test_dispatch_lanes_lock_lost_cancels_lanes_not_yet_started(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A lost lock stops the pass immediately: the queued lane never dispatches."""
    _patch_readiness(monkeypatch)
    monkeypatch.setattr(supervise.runner, "select_runner", lambda *_a, **_k: _MANUAL_SPEC)
    monkeypatch.setattr(supervise, "HEARTBEAT_INTERVAL_S", 0.01)
    release = threading.Event()
    started: list[str] = []

    def fake_dispatch(_repo, _session, lane, _spec, _sizing) -> supervise.LaneOutcome:
        started.append(lane.issue_id)
        assert release.wait(timeout=5)
        return _outcome(lane.issue_id)

    def beat() -> None:
        raise LockLostError("successor took over")

    try:
        with pytest.raises(LockLostError):
            supervise.dispatch_lanes(
                Path(),
                _session(_lane("epic.1"), _lane("epic.2")),
                beat=beat,
                cap=1,
                dispatch_one=fake_dispatch,
            )
    finally:
        release.set()  # let the in-flight worker finish
    assert started == ["epic.1"]


def test_dispatch_lanes_without_ready_lanes_is_a_no_op(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every lane blocked means nothing dispatches and nothing loads."""
    _patch_readiness(monkeypatch, blocked={"epic.1"})
    assert supervise.dispatch_lanes(Path(), _session(_lane("epic.1"))) == ()


# --- The lane worker: bundle, run, record, meter --------------------------------


def _codex_events(tokens: int) -> str:
    event = {"type": "turn.completed", "usage": {"input_tokens": tokens, "output_tokens": 0}}
    return json.dumps(event)


def _codex() -> runner.RunnerSpec:
    return next(s for s in runner.BUILTIN_RUNNERS if s.name == "codex")


def _worker_fixture(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, stdout: str, returncode: int = 0
) -> tuple[_FakeBr, dict]:
    br = _FakeBr(_overrun_issues())
    monkeypatch.setattr(supervise, "_run_br", br)
    seen: dict = {}

    class _WtSession:
        worktree_path = str(tmp_path / "wt")

    (tmp_path / "wt").mkdir(exist_ok=True)
    monkeypatch.setattr(supervise.worktree, "load_session", lambda *_a, **_k: _WtSession())

    def fake_run(spec, prompt, cwd, **_kw):
        seen["prompt"] = prompt
        seen["cwd"] = cwd
        return runner.RunResult(
            spec.name,
            (spec.name,),
            executed=True,
            returncode=returncode,
            stdout=stdout,
            duration_s=0.1,
        )

    monkeypatch.setattr(supervise.runner, "run", fake_run)
    monkeypatch.setattr(
        supervise.loop, "record_run", lambda *a, **_k: seen.setdefault("recorded", a[1])
    )
    return br, seen


def test_dispatch_lane_green_path_meters_and_records(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A clean run records telemetry by bead, meters occupancy, and lands green."""
    codex = _codex()
    br, seen = _worker_fixture(monkeypatch, tmp_path, stdout=_codex_events(50_000))

    outcome = supervise._dispatch_lane(
        tmp_path, _session(_lane("epic.1")), _lane("epic.1"), codex, _sizing()
    )

    assert seen["recorded"] == "epic.1"  # telemetry keyed by the bead
    assert "epic.1" in seen["prompt"]
    assert outcome.occupancy == 50_000
    assert outcome.overrun is False
    assert outcome.followup_id is None
    assert outcome.detail == "finished; ready to land"
    assert br.created == []


def test_dispatch_lane_overrun_triggers_the_finalize_protocol(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Crossing the ceiling spins the remainder into a follow-up bead (D8/7.6)."""
    codex = _codex()
    br, _seen = _worker_fixture(monkeypatch, tmp_path, stdout=_codex_events(250_000))

    outcome = supervise._dispatch_lane(
        tmp_path, _session(_lane("epic.1")), _lane("epic.1"), codex, _sizing()
    )

    assert outcome.overrun is True
    assert outcome.followup_id == "new-1"
    assert "new-1" in outcome.detail
    assert len(br.created) == 1


def test_dispatch_lane_surfaces_the_needs_input_sentinel(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The agent's missing-fact signal is consumed and carried on the outcome."""
    codex = _codex()
    _br, _seen = _worker_fixture(monkeypatch, tmp_path, stdout=_codex_events(10))
    sentinel = tmp_path / "wt" / needs_input.SENTINEL_FILE
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.write_text('{"fact": "which API version", "detail": "docs conflict"}')

    traced: list[tuple[str, str]] = []
    monkeypatch.setattr(
        supervise.policy,
        "record_needs_input",
        lambda _r, issue, fact: traced.append((issue, fact)),
    )
    queued: list[tuple[str, str]] = []
    monkeypatch.setattr(
        supervise.decisions,
        "enqueue",
        lambda _r, issue, kind, *_a, **_k: queued.append((issue, kind)),
    )
    outcome = supervise._dispatch_lane(
        tmp_path, _session(_lane("epic.1")), _lane("epic.1"), codex, _sizing()
    )

    assert traced == [("epic.1", "which API version")]
    assert queued == [("epic.1", "needs-input")]
    assert outcome.needs_fact == "which API version"
    assert "docs conflict" in outcome.detail
    assert not sentinel.exists()  # consumed so a re-dispatch starts clean


def test_dispatch_lane_without_worktree_record_asks_for_reprovision(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A bound lane whose worktree record vanished must not dispatch blind."""
    codex = _codex()
    monkeypatch.setattr(supervise.worktree, "load_session", lambda *_a, **_k: None)

    outcome = supervise._dispatch_lane(
        tmp_path, _session(_lane("epic.1")), _lane("epic.1"), codex, _sizing()
    )

    assert outcome.result is None
    assert "re-provision" in outcome.detail


def test_dispatch_lane_failed_run_never_spins_a_followup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A crashed runner with high usage never spins a follow-up bead.

    It lands nothing coherent and the routing layer re-dispatches it, so no
    remainder may be pinned by the idempotence marker (design 7.6).
    """
    br, _seen = _worker_fixture(monkeypatch, tmp_path, stdout=_codex_events(250_000), returncode=3)

    outcome = supervise._dispatch_lane(
        tmp_path, _session(_lane("epic.1")), _lane("epic.1"), _codex(), _sizing()
    )

    assert outcome.overrun is True  # the metered truth is still reported
    assert outcome.followup_id is None
    assert br.created == []
    assert outcome.detail == "runner exited 3"


def test_dispatch_lanes_contains_a_lane_failure_to_its_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One lane's br hiccup must not discard the other lanes' outcomes."""
    _patch_readiness(monkeypatch)
    monkeypatch.setattr(supervise.runner, "select_runner", lambda *_a, **_k: _MANUAL_SPEC)

    def flaky_dispatch(_repo, _session, lane, _spec, _sizing) -> supervise.LaneOutcome:
        if lane.issue_id == "epic.1":
            raise RuntimeError("br: database is locked")
        return _outcome(lane.issue_id)

    outcomes = supervise.dispatch_lanes(
        Path(),
        _session(_lane("epic.1"), _lane("epic.2")),
        cap=2,
        dispatch_one=flaky_dispatch,
    )

    assert [o.issue_id for o in outcomes] == ["epic.1", "epic.2"]
    assert "lane dispatch failed: br: database is locked" in outcomes[0].detail
    assert outcomes[1].detail == "test"


def test_parse_found_info_bounds_summary_and_detail() -> None:
    """Agent-authored record fields are truncated at parse time.

    A runaway comment must not bloat every later lane's dispatch prompt.
    """
    payload = json.dumps({"kind": "fact", "summary": "s" * 1000, "detail": "d" * 5000})
    info = supervise.parse_found_info(f"{supervise.INFO_MARKER} {payload}", "epic.1")
    assert info is not None
    assert len(info.summary) == 200
    assert len(info.detail) == 500


# --- Outcome routing (basicly-kjc5.7): green lands, everything else queues -----


def _executed_outcome(issue_id: str, *, returncode: int | None = 0, **kw) -> supervise.LaneOutcome:
    result = runner.RunResult(
        "codex",
        ("codex",),
        executed=True,
        returncode=returncode,
        timed_out=kw.pop("timed_out", False),
    )
    return supervise.LaneOutcome(
        issue_id=issue_id,
        runner_name="codex",
        result=result,
        needs_fact=kw.pop("needs_fact", None),
        occupancy=None,
        overrun=False,
        followup_id=None,
        detail=kw.pop("detail", "finished; ready to land"),
    )


def _advance_result(issue_id: str, action: str, to_phase: str, detail: str = ""):
    return loop.AdvanceResult(issue_id, "build", to_phase, action, detail)


def test_route_green_lane_lands_and_ships_under_a_grant(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Green -> loop.advance lands it; a covering grant ships it hands-free."""
    monkeypatch.setattr(
        supervise.loop,
        "advance",
        lambda _r, issue_id, **_k: _advance_result(issue_id, "merged", "verify", "landed"),
    )
    monkeypatch.setattr(
        supervise.policy,
        "approve_checkpoint_guarded",
        lambda *_a, **_k: policy.ApprovalResult("approved", detail="delegated under L3 grant"),
    )
    monkeypatch.setattr(
        supervise.loop,
        "run_until_blocked",
        lambda _r, issue_id, **_k: [_advance_result(issue_id, "tore-down", "done", "closed")],
    )

    routed = supervise.route_outcomes(
        tmp_path, _session(_lane("epic.1")), (_executed_outcome("epic.1"),)
    )

    assert [r.route for r in routed] == ["shipped"]
    assert routed[0].progressed


def test_route_green_lane_without_a_grant_queues_the_ship_checkpoint(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No covering grant: the landing sticks, ship waits on a queued human item."""
    monkeypatch.setattr(
        supervise.loop,
        "advance",
        lambda _r, issue_id, **_k: _advance_result(issue_id, "merged", "verify", "landed"),
    )
    monkeypatch.setattr(
        supervise.policy,
        "approve_checkpoint_guarded",
        lambda *_a, **_k: policy.ApprovalResult("challenge", code="abc"),
    )
    queued: list[tuple[str, str]] = []
    monkeypatch.setattr(
        supervise.decisions,
        "enqueue",
        lambda _r, issue, kind, *_a, **_k: (
            queued.append((issue, kind)),
            decisions_item(issue, kind),
        )[1],
    )

    routed = supervise.route_outcomes(
        tmp_path, _session(_lane("epic.1")), (_executed_outcome("epic.1"),)
    )

    assert routed[0].route == "merged"
    assert queued == [("epic.1", "checkpoint")]
    assert "awaits a human" in routed[0].detail


def decisions_item(issue: str, kind: str) -> supervise.decisions.DecisionItem:
    """A minimal queue item for enqueue fakes."""
    return supervise.decisions.DecisionItem(
        decision_id=f"{issue}#abc", issue_id=issue, kind=kind, question="q"
    )


def test_route_failed_dispatch_retries_then_escalates_at_the_cap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A crashing runner gets the bounded rework loop, not an instant human stop."""
    attempts = {"n": 0}

    def record(_r, _i, gate):
        assert gate == supervise.DISPATCH_GATE
        attempts["n"] += 1
        return attempts["n"]

    monkeypatch.setattr(supervise.policy, "record_rework", record)
    monkeypatch.setattr(
        supervise.policy,
        "load_policy",
        lambda _r: PolicyConfig(required_gates=("verify",), max_rework=2),
    )
    queued: list[str] = []
    monkeypatch.setattr(
        supervise.decisions,
        "enqueue",
        lambda _r, issue, kind, *_a, **_k: (queued.append(kind), decisions_item(issue, kind))[1],
    )
    failed = _executed_outcome("epic.1", returncode=3, detail="runner exited 3")

    first = supervise.route_outcomes(tmp_path, _session(_lane("epic.1")), (failed,))
    second = supervise.route_outcomes(tmp_path, _session(_lane("epic.1")), (failed,))

    assert first[0].route == "retry"
    assert second[0].route == "decision"
    assert queued == ["escalation"]


def test_route_needs_input_and_stall_hold_for_the_queue(tmp_path: Path) -> None:
    """Items queued at dispatch time just park the lane; nothing lands."""
    needs = _executed_outcome("epic.1", needs_fact="which db?", detail="needs input")
    stalled = _executed_outcome("epic.2", returncode=None, timed_out=True, detail="timed out")

    routed = supervise.route_outcomes(
        tmp_path, _session(_lane("epic.1"), _lane("epic.2")), (needs, stalled)
    )

    assert [r.route for r in routed] == ["decision", "decision"]
    assert not any(r.progressed for r in routed)


def test_route_handoff_stays_with_the_driving_agent(tmp_path: Path) -> None:
    """Interactive mode: a handoff lane is not a queue item, it is the human's turn."""
    handoff = supervise.LaneOutcome(
        issue_id="epic.1",
        runner_name="manual",
        result=runner.RunResult("manual", (), executed=False, handoff=True),
        needs_fact=None,
        occupancy=None,
        overrun=False,
        followup_id=None,
        detail="handoff runner: work left to the driving agent",
    )
    routed = supervise.route_outcomes(tmp_path, _session(_lane("epic.1")), (handoff,))
    assert [r.route for r in routed] == ["handoff"]


def test_ready_lanes_skip_lanes_waiting_on_a_decision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A queued judgment holds the lane: re-dispatching would only re-block."""
    _patch_readiness(monkeypatch)
    monkeypatch.setattr(supervise.decisions, "has_pending", lambda _r, issue: issue == "epic.1")
    ready = supervise.ready_lanes(Path(), _session(_lane("epic.1"), _lane("epic.2")))
    assert [lane.issue_id for lane in ready] == ["epic.2"]


def test_dispatch_lane_timeout_queues_a_stall(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A hard-killed dispatch routes to the decision queue as a stall flag."""
    codex = _codex()
    _br, _seen = _worker_fixture(monkeypatch, tmp_path, stdout="")

    def timed_out_run(spec, _prompt, _cwd, **_k):
        return runner.RunResult(spec.name, (spec.name,), executed=True, timed_out=True)

    monkeypatch.setattr(supervise.runner, "run", timed_out_run)
    queued: list[tuple[str, str]] = []
    monkeypatch.setattr(
        supervise.decisions,
        "enqueue",
        lambda _r, issue, kind, *_a, **_k: (
            queued.append((issue, kind)),
            decisions_item(issue, kind),
        )[1],
    )

    outcome = supervise._dispatch_lane(
        tmp_path, _session(_lane("epic.1")), _lane("epic.1"), codex, _sizing()
    )

    assert queued == [("epic.1", "stall")]
    assert "timed out" in outcome.detail
    assert outcome.result is not None and outcome.result.timed_out


# --- Review hardening (kjc5.7): rework routes, held lanes, parked advance -------


def test_route_landing_rework_block_is_retriable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A verify failure under the cap routes 'rework' and keeps the loop running."""
    monkeypatch.setattr(
        supervise.loop,
        "advance",
        lambda _r, issue_id, **_k: loop.AdvanceResult(
            issue_id, "build", "build", "blocked", "verify failed: pytest (rework 1/2)"
        ),
    )
    routed = supervise.route_outcomes(
        tmp_path, _session(_lane("epic.1")), (_executed_outcome("epic.1"),)
    )
    assert [r.route for r in routed] == ["rework"]
    assert supervise.should_continue(routed) is True


def test_route_landing_escalation_parks_on_the_queue(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """At the rework cap the landing escalated (item queued); the loop stops."""
    monkeypatch.setattr(
        supervise.loop,
        "advance",
        lambda _r, issue_id, **_k: loop.AdvanceResult(
            issue_id, "build", "build", "escalated", "verify failed (rework 2/2)"
        ),
    )
    routed = supervise.route_outcomes(
        tmp_path, _session(_lane("epic.1")), (_executed_outcome("epic.1"),)
    )
    assert [r.route for r in routed] == ["decision"]
    assert supervise.should_continue(routed) is False


def test_route_uncommitted_green_run_is_bounded_by_dispatch_rework(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Merge's not-ready guard must not re-dispatch forever un-counted."""
    monkeypatch.setattr(
        supervise.loop,
        "advance",
        lambda _r, issue_id, **_k: loop.AdvanceResult(
            issue_id, "build", "build", "blocked", "commit the work on 'harness/x' before landing"
        ),
    )
    monkeypatch.setattr(supervise.policy, "record_rework", lambda *_a: 1)
    monkeypatch.setattr(
        supervise.policy,
        "load_policy",
        lambda _r: PolicyConfig(required_gates=("verify",), max_rework=2),
    )
    routed = supervise.route_outcomes(
        tmp_path, _session(_lane("epic.1")), (_executed_outcome("epic.1"),)
    )
    assert [r.route for r in routed] == ["retry"]


def test_route_holds_later_green_lanes_after_a_blocked_landing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Stop-on-first-failure: matching merge_queue, later lanes wait a pass."""
    monkeypatch.setattr(
        supervise.loop,
        "advance",
        lambda _r, issue_id, **_k: loop.AdvanceResult(
            issue_id, "build", "build", "blocked", "verify failed (rework 1/2)"
        ),
    )
    outcomes = (_executed_outcome("epic.1"), _executed_outcome("epic.2"))
    routed = supervise.route_outcomes(
        tmp_path, _session(*(_lane(o.issue_id) for o in outcomes)), outcomes
    )
    assert [r.route for r in routed] == ["rework", "held"]
    assert supervise.should_continue(routed) is True


def test_route_contains_a_landing_infra_failure_to_its_lane(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """One br hiccup is contained to its lane; later greens hold for next pass.

    The raised pass is gone, but stop-on-first-failure still applies — the
    held lane re-lands next iteration.
    """

    def flaky_advance(_r, issue_id, **_k):
        if issue_id == "epic.1":
            raise RuntimeError("br: database is locked")
        return loop.AdvanceResult(issue_id, "build", "verify", "merged", "landed")

    monkeypatch.setattr(supervise.loop, "advance", flaky_advance)
    outcomes = (_executed_outcome("epic.1"), _executed_outcome("epic.2"))

    routed = supervise.route_outcomes(
        tmp_path, _session(*(_lane(o.issue_id) for o in outcomes)), outcomes
    )

    assert [r.route for r in routed] == ["error", "held"]
    assert "database is locked" in routed[0].detail


def test_advance_parked_ships_a_verify_lane_without_a_runner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An approved parked lane advances through the engine; no fresh dispatch."""
    phases = {"epic.1": "verify", "epic.2": "build"}
    monkeypatch.setattr(supervise, "_phase_of", lambda _r, issue: phases[issue])
    monkeypatch.setattr(supervise.decisions, "has_pending", lambda _r, _issue: False)
    advanced: list[str] = []

    def fake_run_until_blocked(_r, issue_id, **_k):
        advanced.append(issue_id)
        return [loop.AdvanceResult(issue_id, "ship", "done", "tore-down", "closed")]

    monkeypatch.setattr(supervise.loop, "run_until_blocked", fake_run_until_blocked)

    routed = supervise.advance_parked(tmp_path, _session(_lane("epic.1"), _lane("epic.2")))

    assert advanced == ["epic.1"]  # the build lane is dispatch's business
    assert [r.route for r in routed] == ["shipped"]


def test_advance_parked_skips_lanes_waiting_on_a_decision(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A parked lane whose ship request is still queued stays parked."""
    monkeypatch.setattr(supervise, "_phase_of", lambda _r, _i: "verify")
    monkeypatch.setattr(supervise.decisions, "has_pending", lambda _r, _i: True)
    monkeypatch.setattr(
        supervise.loop,
        "run_until_blocked",
        lambda *_a, **_k: pytest.fail("must not advance a lane awaiting judgment"),
    )
    assert supervise.advance_parked(tmp_path, _session(_lane("epic.1"))) == ()


def test_heartbeat_thread_keeps_the_lock_fresh_and_captures_loss(tmp_path: Path) -> None:
    """The background beater refreshes mtime and stands down on a takeover."""
    lock = supervise.acquire(tmp_path, "epic:hb", "epic")
    hb = supervise.HeartbeatThread(lock, "epic:hb", interval=0.01)
    hb.start()
    try:
        _backdate(lock, STALE_AFTER_S - 1)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if time.time() - lock.stat().st_mtime < 1:
                break
            time.sleep(0.01)
        hb.check()  # still the holder: nothing lost
        assert time.time() - lock.stat().st_mtime < STALE_AFTER_S

        lock.write_text('{"session_id": "epic:successor"}', encoding="utf-8")
        deadline = time.monotonic() + 5
        while hb.lost is None and time.monotonic() < deadline:
            time.sleep(0.01)
        with pytest.raises(LockLostError):
            hb.check()
    finally:
        hb.stop()
        hb.join(timeout=5)
