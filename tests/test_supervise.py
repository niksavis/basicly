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

import ast
import json
import os
import subprocess
import threading
import time
from pathlib import Path

import pytest

from basicly import (
    decisions,
    decompose,
    loop,
    loop_state,
    merge,
    needs_input,
    policy,
    run_record,
    runner,
    supervise,
)
from basicly.config import PolicyConfig, RunnerConfig, SizingConfig
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


def _install_br(monkeypatch: pytest.MonkeyPatch, fake: object) -> None:
    """Route both br aliases build_bundle reads through to one fake.

    ``build_bundle`` scans found-info via supervise's alias and the lane's
    answered decisions via decisions' own — each module's alias is the seam.
    """
    monkeypatch.setattr(supervise, "_run_br", fake)
    monkeypatch.setattr(decisions, "_run_br", fake)


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
        _install_br(mp, br)
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


# --- Coupling discoveries become graph edges (basicly-kjc5.24, D6/7.4) ---------


def _coupling_session(*, in_flight: tuple[str, ...] = ()) -> supervise.SessionState:
    """A two-child session where only *in_flight* children hold a live worktree."""
    return supervise.SessionState(
        root_issue="epic",
        root_status="open",
        children=(("epic.1", "in_progress"), ("epic.2", "open")),
        adopted=tuple(_lane(issue_id) for issue_id in in_flight),
    )


def _coupling_issues(scope: str = "", deps: tuple[dict, ...] = ()) -> dict[str, dict]:
    """Two children; epic.2 optionally declares *scope* and carries *deps*."""
    return {
        "epic": _issue("epic", children=(("epic.1", "in_progress"), ("epic.2", "open"))),
        "epic.1": _issue("epic.1", "in_progress"),
        "epic.2": {
            "id": "epic.2",
            "status": "open",
            "issue_type": "task",
            "description": f"Do the work.\n\n## Scope\n\n- `{scope}`\n" if scope else "Do it.",
            "dependencies": list(deps),
        },
    }


def _propose(
    monkeypatch: pytest.MonkeyPatch,
    fake: _FakeBr,
    session: supervise.SessionState,
) -> tuple[tuple[str, str, str], ...]:
    """Run the proposal with every br seam this path uses pointed at *fake*."""
    _install_br(monkeypatch, fake)
    monkeypatch.setattr(supervise, "_try_run_br", fake)
    monkeypatch.setattr(supervise.decompose, "_run_br", fake)
    monkeypatch.setattr(supervise.merge.br, "try_run_br", fake)
    return supervise.propose_coupling_edges(Path(), session)


def test_a_coupling_record_gates_a_bead_that_has_not_started(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing is lost by making unstarted work wait, and the collision is prevented."""
    fake = _FakeBr(
        _coupling_issues(),
        comments={"epic.1": [_fold_marker("coupling", "both read the loader", ["epic.2"])]},
    )

    recorded = _propose(monkeypatch, fake, _coupling_session(in_flight=("epic.1",)))

    assert recorded == (("epic.2", "epic.1", "blocks"),)
    assert ("epic.2", "epic.1", "-t", "blocks") in fake.deps


def test_a_coupling_record_teaches_an_in_flight_lane_without_gating_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A lane with committed work must not be stranded by what it just learned.

    The grrb lesson applied to the discovery path: gating a live lane drops it out
    of ready_lanes and holds it behind a human, so the coupling is recorded
    non-gating and reaches that lane as a folded record instead.
    """
    fake = _FakeBr(
        _coupling_issues(),
        comments={"epic.1": [_fold_marker("coupling", "both read the loader", ["epic.2"])]},
    )

    recorded = _propose(monkeypatch, fake, _coupling_session(in_flight=("epic.1", "epic.2")))

    assert recorded == (("epic.2", "epic.1", supervise.merge.COUPLING_DEP_TYPE),)
    assert not any(dep[-1] == "blocks" for dep in fake.deps)


def test_a_coupling_record_reaches_a_bead_by_scope_overlap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A record naming paths, not ids, still finds whoever declared those paths."""
    fake = _FakeBr(
        _coupling_issues(scope="src/basicly/loop.py"),
        comments={
            "epic.1": [_fold_marker("coupling", "the loader is shared", ["src/basicly/loop.py"])]
        },
    )

    recorded = _propose(monkeypatch, fake, _coupling_session(in_flight=("epic.1",)))

    assert recorded == (("epic.2", "epic.1", "blocks"),)


def test_only_a_coupling_record_proposes_an_edge(monkeypatch: pytest.MonkeyPatch) -> None:
    """The other kinds are advisory context; only `coupling` implies an ordering."""
    fake = _FakeBr(
        _coupling_issues(),
        comments={
            "epic.1": [
                _fold_marker("fact", "the loader caches", ["epic.2"]),
                _fold_marker("constraint", "keep the window", ["epic.2"]),
                _fold_marker("decision", "we chose toml", ["epic.2"]),
            ]
        },
    )

    assert _propose(monkeypatch, fake, _coupling_session(in_flight=("epic.1",))) == ()
    assert fake.deps == []


def test_a_coupling_edge_is_recorded_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """Re-reading the record every pass must not re-issue the edge."""
    existing = ({"id": "epic.1", "dependency_type": "blocks"},)
    fake = _FakeBr(
        _coupling_issues(deps=existing),
        comments={"epic.1": [_fold_marker("coupling", "both read the loader", ["epic.2"])]},
    )

    assert _propose(monkeypatch, fake, _coupling_session(in_flight=("epic.1",))) == ()
    assert fake.deps == []


def test_a_coupling_record_never_couples_its_source_to_itself(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A lane naming its own bead (or its own scope) proposes nothing."""
    fake = _FakeBr(
        _coupling_issues(),
        comments={"epic.1": [_fold_marker("coupling", "note to self", ["epic.1"])]},
    )

    assert _propose(monkeypatch, fake, _coupling_session(in_flight=("epic.1",))) == ()
    assert fake.deps == []


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
    _install_br(monkeypatch, br)

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
    _install_br(monkeypatch, br)

    bundle = supervise.build_bundle(Path(), "epic.1", known_ids=frozenset({"epic", "epic.2"}))

    assert bundle.folded == ()


def test_build_bundle_sees_records_published_after_planning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Assembly reads br at call time (fresh at boundaries, never mid-flight — D6).

    A record landing between dispatches folds into the later bundle.
    """
    br = _FakeBr(_bundle_issues())
    _install_br(monkeypatch, br)
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


def _dispatch_sizing(total: int, scope_tokens: int = 1_000) -> decompose.DispatchSizing:
    """A lane sizing whose working set is exactly *total* tokens.

    Built by naming the total the band is judged against, so a band test never has
    to reproduce ``CostEstimate``'s overhead-plus-factor arithmetic to land on one
    side of a threshold.
    """
    return decompose.DispatchSizing(
        task_class="task",
        estimate=decompose.CostEstimate(
            scope_tokens=scope_tokens, overhead_tokens=total - scope_tokens, build_factor=1.0
        ),
        source=decompose.DISPATCH_FORECAST,
    )


def _lookup(
    sizing: decompose.DispatchSizing | None, absence: str = decompose.SCOPE_UNREADABLE
) -> decompose.SizingLookup:
    """What the estimator answers for one lane: a sizing, or which absence it hit.

    *absence* defaults to the unreadable case, which is the answer that admits and
    escalates nothing — so a test that is not about sizing keeps saying what it said
    before the undeclared case was split out (basicly-jr0l.60).
    """
    return decompose.SizingLookup(sizing, "" if sizing is not None else absence)


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
            # A real bead always carries a priority and may carry labels; the
            # fixture used to omit both, which is why the dropped-classification
            # defect was invisible to this suite (basicly-jr0l.25). P0 is chosen
            # deliberately: it differs from br's default of 2, so a regression
            # that stops passing ``-p`` shows up as a value mismatch rather than
            # coincidentally matching.
            "priority": 0,
            "labels": ["phase-7", "determinism"],
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
    _install_br(monkeypatch, br)

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
    _install_br(monkeypatch, br)

    first = supervise.finalize_followup(
        Path(), "epic", "epic.1", occupancy=130_000, ceiling=120_000
    )
    second = supervise.finalize_followup(
        Path(), "epic", "epic.1", occupancy=131_000, ceiling=120_000
    )

    assert first == second == "new-1"
    assert len(br.created) == 1


def test_finalize_followup_body_carries_the_inherited_types_sections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bug follow-up owes Steps to Reproduce, or the engine creates a bead its own gate refuses.

    The body used to be hand-written with the task section set, so a bug lane's
    overrun produced a top-level package that `policy.definition_of_ready` then
    blocked at classify (basicly-kjc5.44).
    """
    issues = _overrun_issues()
    issues["epic.1"]["issue_type"] = "bug"
    br = _FakeBr(issues)
    _install_br(monkeypatch, br)

    supervise.finalize_followup(Path(), "epic", "epic.1", occupancy=130_000, ceiling=120_000)

    create = br.created[0]
    assert create[create.index("-t") + 1] == "bug"
    body = create[create.index("-d") + 1]
    headings = [line for line in body.splitlines() if line.startswith("## ")]
    assert headings == ["## Steps to Reproduce", "## Acceptance Criteria", "## Scope"]
    # The carried-over context still leads, above the structure.
    assert body.startswith("Continues epic.1:")
    assert "- parses all three formats" in body and "- `src/a/**`" in body


def test_finalize_followup_carries_the_overrun_beads_priority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A P0 lane's remainder must not come back as P2.

    ``br create`` defaults an omitted priority to 2 and the call site passed no
    ``-p``, so the continuation of the most urgent work in a pass was ranked
    behind every routine bead in the ready set — the scheduler orders by
    priority. Invisible until now because all three follow-ups the engine has
    produced continued P2 parents, so the default happened to match
    (basicly-jr0l.25).
    """
    br = _FakeBr(_overrun_issues())
    _install_br(monkeypatch, br)

    supervise.finalize_followup(Path(), "epic", "epic.1", occupancy=130_000, ceiling=120_000)

    create = br.created[0]
    assert create[create.index("-p") + 1] == "0"


def test_finalize_followup_carries_the_overrun_beads_labels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Phase membership is a label, so a dropped label removes the bead from the phase.

    An unlabelled follow-up is well-formed, passes its own DoR, and is absent
    from every ``br list --label phase-N`` — so a planning pass built on the
    label cannot see it at all (basicly-jr0l.25).
    """
    br = _FakeBr(_overrun_issues())
    _install_br(monkeypatch, br)

    supervise.finalize_followup(Path(), "epic", "epic.1", occupancy=130_000, ceiling=120_000)

    create = br.created[0]
    assert create[create.index("-l") + 1] == "phase-7,determinism"


def test_finalize_followup_omits_classification_flags_the_overrun_bead_lacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unlabelled or priority-less parent must not send an empty flag value.

    ``-l ''`` is not the same request as omitting ``-l``, and a bead read back
    from a tracker that has never set a label returns null rather than a list.
    """
    issues = _overrun_issues()
    issues["epic.1"]["labels"] = None
    del issues["epic.1"]["priority"]
    br = _FakeBr(issues)
    _install_br(monkeypatch, br)

    supervise.finalize_followup(Path(), "epic", "epic.1", occupancy=130_000, ceiling=120_000)

    create = br.created[0]
    assert "-l" not in create
    assert "-p" not in create


def test_finalize_followup_leaf_root_creates_without_parent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the overrun lane is the session root itself there is no parent to nest under."""
    issues = _overrun_issues()
    issues["epic.1"]["issue_type"] = "feature"  # non-leaf type falls back to task
    br = _FakeBr(issues)
    _install_br(monkeypatch, br)

    supervise.finalize_followup(Path(), "epic.1", "epic.1", occupancy=1, ceiling=1)

    create = br.created[0]
    assert "--parent" not in create
    assert create[create.index("-t") + 1] == "task"


def test_finalize_followup_keeps_every_flag_next_to_its_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A child lane's follow-up must not separate ``-t`` from the type it names.

    The parent used to be spliced in at a fixed index that was the *value* of
    ``-t``, so a child lane emitted ``-t --parent <root> <type>`` and br refused
    it for a missing type. The overrun follow-up was never created, the lane
    reported a failed dispatch, and the work its agent had already committed
    never landed (basicly-jr0l.11, found by the basicly-kjc5.22 dogfood).

    The sibling leaf-root test above asserts this same pairing — but only on the
    branch where no parent is inserted, which is the one branch that could not
    break. This asserts it where the splice actually happened, and over every
    value-taking flag, so adding the next one here cannot recreate the shape.
    """
    br = _FakeBr(_overrun_issues())
    _install_br(monkeypatch, br)

    supervise.finalize_followup(Path(), "epic", "epic.1", occupancy=130_000, ceiling=120_000)

    create = br.created[0]
    assert create[create.index("-t") + 1] == "task"
    for flag in ("-t", "-p", "-l", "--parent", "-d"):
        value = create[create.index(flag) + 1]
        assert not value.startswith("-"), f"{flag} is followed by {value!r}, not a value"


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
    # One ranking behind both accessors: `ready_lanes` orders with `ready_ranked`
    # while `dispatch_lanes` records the envelope from `ready_ranking`, and a fake
    # answering them differently would not be a state the supervisor can reach.
    ranking = loop_state.Ranking(
        nodes=tuple(
            loop_state.RankedNode(
                rank=rank, score=rank * 10, issue_id=iid, title="", fallback_rank=rank
            )
            for rank, iid in ranked
        ),
        schema="br.scheduler.v1",
        fallback_sort="priority ASC, created_at ASC, id ASC",
    )
    monkeypatch.setattr(supervise.loop_state, "ready_ranking", lambda _r, *_a: ranking)
    monkeypatch.setattr(supervise.loop_state, "ready_ranked", lambda _r: ranking.nodes)
    monkeypatch.setattr(supervise.decisions, "has_pending", lambda _r, _i: False)
    monkeypatch.setattr(supervise, "_phase_of", lambda _r, _i: "build")
    monkeypatch.setattr(supervise, "_has_subtasks", lambda _r, _i: False)
    # Ungranted sessions have no ceiling to enforce, which is the state these
    # tests are about; the halt itself is pinned separately, below.
    monkeypatch.setattr(supervise.policy, "spend_status", lambda *_a, **_k: _UNGRANTED)
    # A pass now sizes every lane before it dispatches any (basicly-jr0l.22), so an
    # unstubbed estimator here would spawn a real `br` per lane in tests that are
    # about scheduling — the same trap an unstubbed tracker read set for jr0l.16.
    # "Unreadable" admits and escalates nothing, which is the state these tests want;
    # the sizing-dependent behaviour is pinned with explicit sizings below.
    monkeypatch.setattr(supervise.decompose, "resolve_dispatch_sizing", lambda *_a: _lookup(None))


# No grant, so no D3 ceiling: dispatch admission is not what is under test here.
_UNGRANTED = policy.SpendStatus(grant=None, spent_tokens=0, halted=False)


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

    def fake_dispatch(_repo, _session, lane, _spec, _sizing, **_kw) -> supervise.LaneOutcome:
        with gauge_lock:
            gauge["current"] += 1
            gauge["max"] = max(gauge["max"], gauge["current"])
        barrier.wait()  # both slots must be occupied at once to pass
        with gauge_lock:
            gauge["current"] -= 1
        return _outcome(lane.issue_id)

    monkeypatch.setattr(supervise, "_dispatch_lane", fake_dispatch)

    outcomes = supervise.dispatch_lanes(Path(), _session(*lanes), cap=2)

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

    def fake_dispatch(_repo, _session, lane, _spec, _sizing, **_kw) -> supervise.LaneOutcome:
        assert release.wait(timeout=5)
        return _outcome(lane.issue_id)

    monkeypatch.setattr(supervise, "_dispatch_lane", fake_dispatch)

    outcomes = supervise.dispatch_lanes(Path(), _session(_lane("epic.1")), beat=beat, cap=1)

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

    def fake_dispatch(_repo, _session, lane, _spec, _sizing, **_kw) -> supervise.LaneOutcome:
        started.append(lane.issue_id)
        assert release.wait(timeout=5)
        return _outcome(lane.issue_id)

    monkeypatch.setattr(supervise, "_dispatch_lane", fake_dispatch)

    def beat() -> None:
        raise LockLostError("successor took over")

    try:
        with pytest.raises(LockLostError):
            supervise.dispatch_lanes(
                Path(),
                _session(_lane("epic.1"), _lane("epic.2")),
                beat=beat,
                cap=1,
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
    """A codex stream whose last turn occupies *tokens* of the window.

    Deliberately minimal, and deliberately not widened with the cache and
    reasoning counts a real 0.146.0 turn carries (basicly-jr0l.37): these tests
    drive the **context-ceiling** meter, which reads `input_tokens` alone because
    that is the whole window re-sent that turn. Naming a ceiling fraction here
    directly is what makes the threshold assertions readable; the observed
    full-envelope fixture lives with the extractor, in `tests/test_runner.py`.
    """
    event = {"type": "turn.completed", "usage": {"input_tokens": tokens, "output_tokens": 0}}
    return json.dumps(event)


def _codex() -> runner.RunnerSpec:
    return next(s for s in runner.BUILTIN_RUNNERS if s.name == "codex")


def _worker_fixture(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, stdout: str, returncode: int = 0
) -> tuple[_FakeBr, dict]:
    br = _FakeBr(_overrun_issues())
    _install_br(monkeypatch, br)
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
    # The lane's sizing read reaches the real br binary (basicly-jr0l.34), and this
    # fixture stands in for the whole dispatch environment. Left live, every
    # supervise test would spawn a subprocess and contend on br's machine-global
    # lock — enough to perturb the stall-watchdog timing tests in this file. Stubbed
    # at the estimator rather than at the recorded keywords, because the band gate
    # now reads the same call (basicly-jr0l.16); the unreadable answer admits the
    # dispatch, records no sizing and queues nothing, exactly as before.
    monkeypatch.setattr(supervise.decompose, "resolve_dispatch_sizing", lambda *_a: _lookup(None))
    return br, seen


def test_dispatch_lane_records_the_scheduler_evidence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The pass ordering must be reconstructible from the record (D9, basicly-vkh0.3)."""
    codex = _codex()
    _worker_fixture(monkeypatch, tmp_path, stdout=_codex_events(50_000))
    captured: dict = {}
    monkeypatch.setattr(supervise.loop, "record_run", lambda *_a, **kw: captured.update(kw))

    supervise._dispatch_lane(
        tmp_path,
        _session(_lane("epic.1")),
        _lane("epic.1"),
        codex,
        _sizing(),
        ordering=supervise.DispatchOrdering(
            dispatch_rank=2,
            node=loop_state.RankedNode(
                rank=1, score=45, issue_id="epic.1", title="", fallback_rank=3
            ),
            policy="br.scheduler.v1",
        ),
    )

    assert captured["dispatch_rank"] == 2
    assert captured["scheduler_rank"] == 1
    assert captured["scheduler_fallback_rank"] == 3
    assert captured["scheduler_score"] == 45
    assert captured["scheduler_policy"] == "br.scheduler.v1"


def test_dispatch_lane_records_the_order_when_br_never_ranked_the_lane(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The common case, not an edge case (basicly-vkh0.3).

    A provisioned lane is claimed and ``br scheduler`` recommends only unclaimed
    work, so most dispatched lanes carry no scheduler evidence at all. The dispatch
    position must still be recorded, or a null would be ambiguous between "br had
    no opinion" and "nobody recorded it".
    """
    codex = _codex()
    _worker_fixture(monkeypatch, tmp_path, stdout=_codex_events(50_000))
    captured: dict = {}
    monkeypatch.setattr(supervise.loop, "record_run", lambda *_a, **kw: captured.update(kw))

    supervise._dispatch_lane(
        tmp_path,
        _session(_lane("epic.1")),
        _lane("epic.1"),
        codex,
        _sizing(),
        ordering=supervise.DispatchOrdering(dispatch_rank=1, node=None, policy="br.scheduler.v1"),
    )

    assert captured["dispatch_rank"] == 1
    assert captured["scheduler_rank"] is None
    assert captured["scheduler_score"] is None
    # The policy is a property of the pass, so it is recorded either way.
    assert captured["scheduler_policy"] == "br.scheduler.v1"


def test_dispatch_lanes_records_one_ranking_for_the_whole_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every lane in a pass is explained against the same ranking, in dispatch order."""
    lanes = (_lane("epic.1"), _lane("epic.2"))
    _patch_readiness(monkeypatch, ranked=((1, "epic.1"), (2, "epic.2")))
    monkeypatch.setattr(supervise.runner, "select_runner", lambda *_a, **_k: _MANUAL_SPEC)
    seen: dict[str, supervise.DispatchOrdering | None] = {}

    def fake_dispatch(_repo, _session, lane, _spec, _sizing, *, ordering=None, **_kw):
        seen[lane.issue_id] = ordering
        return _outcome(lane.issue_id)

    monkeypatch.setattr(supervise, "_dispatch_lane", fake_dispatch)

    supervise.dispatch_lanes(Path(), _session(*lanes), cap=1)

    first, second = seen["epic.1"], seen["epic.2"]
    assert first is not None and second is not None
    assert [first.dispatch_rank, second.dispatch_rank] == [1, 2]
    assert {first.policy, second.policy} == {"br.scheduler.v1"}
    assert first.node is not None
    assert first.node.rank == 1


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

    def flaky_dispatch(_repo, _session, lane, _spec, _sizing, **_kw) -> supervise.LaneOutcome:
        if lane.issue_id == "epic.1":
            raise RuntimeError("br: database is locked")
        return _outcome(lane.issue_id)

    monkeypatch.setattr(supervise, "_dispatch_lane", flaky_dispatch)

    outcomes = supervise.dispatch_lanes(
        Path(),
        _session(_lane("epic.1"), _lane("epic.2")),
        cap=2,
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


def test_a_queued_ship_item_carries_why_the_grant_declined(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The human answering the item is the one who needs the reason (basicly-5ltn).

    In a multi-lane session the violated precondition is usually on another lane's
    bead, so the queued question is unanswerable without it.
    """
    monkeypatch.setattr(
        supervise.loop,
        "advance",
        lambda _r, issue_id, **_k: _advance_result(issue_id, "merged", "verify", "landed"),
    )
    monkeypatch.setattr(
        supervise.policy,
        "approve_checkpoint_guarded",
        lambda *_a, **_k: policy.ApprovalResult(
            "challenge",
            code="abc",
            detail="the active L3 grant covers ship but declined it: escalation on epic.2",
        ),
    )
    details: list[str] = []
    monkeypatch.setattr(
        supervise.decisions,
        "enqueue",
        lambda _r, issue, kind, _question, detail="", **_k: (
            details.append(detail),
            decisions_item(issue, kind),
        )[1],
    )

    supervise.route_outcomes(tmp_path, _session(_lane("epic.1")), (_executed_outcome("epic.1"),))

    assert details == [
        "landed; the active L3 grant covers ship but declined it: escalation on epic.2"
    ]


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
    """Merge's not-ready guard must not re-dispatch forever un-counted.

    The shape is read off the landing result (basicly-kjc5.20), not sniffed out
    of the message text.
    """
    monkeypatch.setattr(
        supervise.loop,
        "advance",
        lambda _r, issue_id, **_k: loop.AdvanceResult(
            issue_id,
            "build",
            "build",
            "blocked",
            "commit the work on 'harness/x' before landing",
            landing=merge.MergeResult("x", "not-ready", "commit the work"),
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


# --- Consume-as-ready routing: bounce a collision, keep landing (kjc5.20) ------


def _blocked_landing(
    issue_id: str, status: str, conflicts: tuple[str, ...] = (), *, escalated: bool = False
) -> loop.AdvanceResult:
    """A blocked landing carrying the merge attempt behind it, as the loop does."""
    return loop.AdvanceResult(
        issue_id,
        "build",
        "build",
        "escalated" if escalated else "blocked",
        f"merge failed: {status}",
        landing=merge.MergeResult(issue_id, status, status, conflicts=conflicts),
    )


def _patch_collision_pass(
    monkeypatch: pytest.MonkeyPatch,
    *,
    collides: str,
    scopes: dict[str, tuple[str, ...]],
) -> list[tuple[str, str]]:
    """A pass where *collides* bounces on ``src/shared.py`` and the rest land.

    Declared scopes come from *scopes*, as ``## Scope`` on each bead would. Returns
    the list the recorded coupling edges accumulate into.
    """

    def advance(_r, issue_id, **_k):
        if issue_id == collides:
            return _blocked_landing(issue_id, "merge-conflicts", ("src/shared.py",))
        return loop.AdvanceResult(issue_id, "build", "verify", "merged", "landed")

    monkeypatch.setattr(supervise.loop, "advance", advance)
    monkeypatch.setattr(supervise, "_landing_order", lambda _r, outcomes: list(outcomes))
    monkeypatch.setattr(supervise.merge, "head_sha", lambda _r: "sha")
    monkeypatch.setattr(supervise.merge, "changed_paths", lambda _r, _before: ("src/shared.py",))
    monkeypatch.setattr(
        supervise.merge.decompose,
        "bead_class_and_scope",
        lambda _r, bead: ("task", scopes[bead]) if bead in scopes else None,
    )
    monkeypatch.setattr(
        supervise.policy,
        "approve_checkpoint_guarded",
        lambda *_a, **_k: policy.ApprovalResult("challenge", code="abc"),
    )
    monkeypatch.setattr(
        supervise.decisions,
        "enqueue",
        lambda _r, issue, kind, *_a, **_k: decisions_item(issue, kind),
    )
    couplings: list[tuple[str, str]] = []

    def try_run_br(_r, args):
        # The edge exactly as `br` receives it, so its *direction* is asserted too.
        if args[:2] == ["dep", "add"]:
            couplings.append((args[2], args[3]))

    monkeypatch.setattr(supervise.merge.br, "try_run_br", try_run_br)
    return couplings


def test_route_bounces_a_collided_lane_and_lands_the_rest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A scope collision is the lane's problem, not the pass's (D5/kjc5.20)."""
    couplings = _patch_collision_pass(
        monkeypatch,
        collides="epic.2",
        scopes={
            "epic.1": ("src/shared.py",),
            "epic.2": ("src/shared.py",),
            "epic.3": ("docs/**",),
        },
    )

    outcomes = tuple(_executed_outcome(f"epic.{n}") for n in (1, 2, 3))
    routed = supervise.route_outcomes(
        tmp_path, _session(*(_lane(o.issue_id) for o in outcomes)), outcomes
    )

    assert [r.route for r in routed] == ["merged", "bounced", "merged"]  # epic.3 not held
    # epic.1 declared the colliding path; epic.3 landed too but its scope is elsewhere.
    assert couplings == [("epic.1", "epic.2")]
    assert "coupling recorded on epic.1" in routed[1].detail
    assert supervise.should_continue(routed) is True


def test_route_records_the_same_coupling_edge_under_either_completion_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """D9: which lane lands first must not decide permanent graph state (kjc5.32).

    Same plan, same declared scopes, the two lanes' completion order reversed — so
    the other one is the bouncer. The recorded edge must be byte-identical, because
    it is derived from the scopes and the conflicting path, not from the order.
    """
    scopes = {"epic.1": ("src/shared.py",), "epic.2": ("src/*.py",)}

    def run(collides: str, order: tuple[str, ...]) -> list[tuple[str, str]]:
        couplings = _patch_collision_pass(monkeypatch, collides=collides, scopes=scopes)
        outcomes = tuple(_executed_outcome(issue_id) for issue_id in order)
        supervise.route_outcomes(
            tmp_path, _session(*(_lane(o.issue_id) for o in outcomes)), outcomes
        )
        return couplings

    # epic.1 finishes first and lands, so epic.2 bounces — then the reverse.
    forward = run("epic.2", ("epic.1", "epic.2"))
    reversed_ = run("epic.1", ("epic.2", "epic.1"))

    assert forward == [("epic.1", "epic.2")]
    assert reversed_ == forward


def test_route_attributes_a_bounce_against_a_lane_that_landed_after_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Attribution is over the whole pass, not the prefix that preceded the bounce.

    The colliding lane routes *second*, so an incremental attribution had nothing
    to blame and recorded no edge at all (kjc5.32).
    """
    couplings = _patch_collision_pass(
        monkeypatch,
        collides="epic.1",
        scopes={"epic.1": ("src/shared.py",), "epic.2": ("src/shared.py",)},
    )

    outcomes = tuple(_executed_outcome(f"epic.{n}") for n in (1, 2))
    routed = supervise.route_outcomes(
        tmp_path, _session(*(_lane(o.issue_id) for o in outcomes)), outcomes
    )

    assert [r.route for r in routed] == ["bounced", "merged"]
    assert couplings == [("epic.1", "epic.2")]
    assert "coupling recorded on epic.2" in routed[0].detail


def test_route_records_no_coupling_onto_a_lane_outside_the_conflicting_scope(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A landing whose declared scope cannot match the conflicting path is not blamed."""
    couplings = _patch_collision_pass(
        monkeypatch,
        collides="epic.2",
        scopes={"epic.1": ("docs/**",), "epic.2": ("src/shared.py",)},
    )

    outcomes = tuple(_executed_outcome(f"epic.{n}") for n in (1, 2))
    routed = supervise.route_outcomes(
        tmp_path, _session(*(_lane(o.issue_id) for o in outcomes)), outcomes
    )

    assert [r.route for r in routed] == ["merged", "bounced"] and couplings == []
    assert "coupling recorded" not in routed[1].detail


def test_route_bounce_records_no_coupling_when_nothing_landed_the_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """With no landing this pass to blame, the bounce records no edge."""
    monkeypatch.setattr(
        supervise.loop,
        "advance",
        lambda _r, issue_id, **_k: _blocked_landing(issue_id, "rebase-conflicts", ("src/a.py",)),
    )
    monkeypatch.setattr(supervise, "_landing_order", lambda _r, outcomes: list(outcomes))
    couplings: list[tuple[str, str]] = []
    monkeypatch.setattr(
        supervise.merge, "record_coupling", lambda _r, bead, on: couplings.append((bead, on))
    )

    routed = supervise.route_outcomes(
        tmp_path, _session(_lane("epic.1")), (_executed_outcome("epic.1"),)
    )

    assert [r.route for r in routed] == ["bounced"] and couplings == []


def test_route_bounce_at_the_rework_cap_parks_on_the_decision_queue(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """At the cap the loop already escalated: the lane waits for a human, not a re-dispatch."""
    monkeypatch.setattr(
        supervise.loop,
        "advance",
        lambda _r, issue_id, **_k: _blocked_landing(
            issue_id, "merge-conflicts", ("src/a.py",), escalated=True
        ),
    )
    monkeypatch.setattr(supervise, "_landing_order", lambda _r, outcomes: list(outcomes))
    monkeypatch.setattr(supervise.merge, "record_coupling", lambda *_a: None)

    routed = supervise.route_outcomes(
        tmp_path, _session(_lane("epic.1")), (_executed_outcome("epic.1"),)
    )

    assert [r.route for r in routed] == ["decision"]
    assert supervise.should_continue(routed) is False


def test_route_still_holds_later_lanes_when_a_gate_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A red suite is not a scope collision: the later green lanes still wait a pass."""
    monkeypatch.setattr(
        supervise.loop,
        "advance",
        lambda _r, issue_id, **_k: _blocked_landing(issue_id, "verify-failed"),
    )
    monkeypatch.setattr(supervise, "_landing_order", lambda _r, outcomes: list(outcomes))
    outcomes = (_executed_outcome("epic.1"), _executed_outcome("epic.2"))

    routed = supervise.route_outcomes(
        tmp_path, _session(*(_lane(o.issue_id) for o in outcomes)), outcomes
    )

    assert [r.route for r in routed] == ["rework", "held"]


def test_route_holds_a_lane_whose_gate_was_merely_unreliable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A failure that did not reproduce carries the lane instead of re-dispatching it.

    The lane is green and committed — the re-run proved it — so routing it to
    ``rework`` would spend a fresh agent dispatch rewriting a correct diff, and
    the ``held`` carry is exactly the "ran out of a landable base" shape
    (basicly-55yh).
    """
    monkeypatch.setattr(
        supervise.loop,
        "advance",
        lambda _r, issue_id, **_k: _blocked_landing(issue_id, merge.VERIFY_UNRELIABLE),
    )
    monkeypatch.setattr(supervise, "_landing_order", lambda _r, outcomes: list(outcomes))
    outcomes = (_executed_outcome("epic.1"),)

    routed = supervise.route_outcomes(
        tmp_path, _session(*(_lane(o.issue_id) for o in outcomes)), outcomes
    )

    assert [r.route for r in routed] == ["held"]
    # `held` is the only carrying route, so the next pass lands it first rather
    # than re-entering dispatch.
    assert supervise.carried_forward(routed) == frozenset({"epic.1"})
    assert supervise.should_continue(routed) is True


# --- Cancel and re-dispatch a lane a landing broke (D6, kjc5.26) --------------


class _WtBinding:
    """A worktree session record: what the merge probe needs to read a lane."""

    def __init__(self, issue_id: str) -> None:
        self.name = issue_id
        self.branch = f"harness/{issue_id}"
        self.base = "main"
        self.worktree_path = f"/tmp/{issue_id}"


def _patch_preempt_pass(
    monkeypatch: pytest.MonkeyPatch,
    *,
    conflicts: dict[str, tuple[str, ...]],
    changed: tuple[str, ...],
    max_rework: int = 2,
    known_rework: int = 0,
) -> dict[str, list]:
    """A pass where every lane lands green unless the merge probe says otherwise.

    *conflicts* is what ``git merge-tree`` reports per lane branch (absent means
    the probe is clean); *changed* is what a landing adds to the base.
    *known_rework* seeds the dispatch counter, as an earlier failure in the
    session would have. Returns the lanes actually landed, the found-info records
    published, the rework gates charged, and the queue items enqueued.
    """
    seen: dict[str, list] = {"landed": [], "info": [], "rework": [], "enqueued": []}

    def advance(_r, issue_id, **_k):
        seen["landed"].append(issue_id)
        return loop.AdvanceResult(issue_id, "build", "verify", "merged", "landed")

    def probe(_r, _base, branch):
        found = conflicts.get(branch.removeprefix("harness/"), ())
        return merge.ProbeResult(safe=not found, conflicts=found)

    def record_rework(_r, issue_id, gate):
        seen["rework"].append((issue_id, gate))
        return known_rework + sum(1 for i, _g in seen["rework"] if i == issue_id)

    monkeypatch.setattr(supervise.loop, "advance", advance)
    monkeypatch.setattr(supervise, "_landing_order", lambda _r, outcomes: list(outcomes))
    monkeypatch.setattr(supervise.merge, "head_sha", lambda _r: "sha")
    monkeypatch.setattr(supervise.merge, "changed_paths", lambda _r, _before: changed)
    monkeypatch.setattr(supervise.merge, "probe_merge", probe)
    monkeypatch.setattr(supervise.worktree, "load_session", lambda name, _r: _WtBinding(name))
    monkeypatch.setattr(supervise.policy, "record_rework", record_rework)
    monkeypatch.setattr(
        supervise.policy,
        "load_policy",
        lambda _r: PolicyConfig(required_gates=("verify",), max_rework=max_rework),
    )
    monkeypatch.setattr(
        supervise.policy,
        "approve_checkpoint_guarded",
        lambda *_a, **_k: policy.ApprovalResult("challenge", code="abc"),
    )

    def enqueue(_r, issue, kind, question, *_a, **_k):
        seen["enqueued"].append((issue, kind, question))
        return decisions_item(issue, kind)

    monkeypatch.setattr(supervise.decisions, "enqueue", enqueue)
    monkeypatch.setattr(
        supervise,
        "record_found_info",
        lambda _r, issue, info: seen["info"].append((issue, info)),
    )
    # A dependency edge here would gate, not merely inform: the lane that landed
    # is merged and not yet shipped, so it is still open.
    monkeypatch.setattr(
        supervise.merge,
        "record_coupling",
        lambda *_a: pytest.fail("the pre-empt must not record a gating dependency edge"),
    )
    return seen


def test_route_cancels_a_lane_whose_merge_the_landing_broke_and_lands_the_rest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The doomed landing is never attempted: the lane owes a fresh dispatch (D6)."""
    seen = _patch_preempt_pass(
        monkeypatch,
        conflicts={"epic.2": ("src/shared.py",)},
        changed=("src/shared.py",),
    )
    outcomes = tuple(_executed_outcome(f"epic.{n}") for n in (1, 2, 3))

    routed = supervise.route_outcomes(
        tmp_path, _session(*(_lane(o.issue_id) for o in outcomes)), outcomes
    )

    assert [r.route for r in routed] == ["merged", "re-dispatch", "merged"]
    # The cancelled lane's landing was skipped entirely, and epic.3 still landed.
    assert seen["landed"] == ["epic.1", "epic.3"]
    assert "epic.1" in routed[1].detail
    # Charged against the same counter a failed dispatch spends, under the cap.
    assert seen["rework"] == [("epic.2", supervise.DISPATCH_GATE)]
    assert "dispatch rework 1/2" in routed[1].detail
    # It is a retriable route, so the pass keeps going and the lane re-dispatches
    # next pass rather than being carried to a landing (kjc5.18).
    assert supervise.should_continue(routed) is True
    assert supervise.carried_forward(routed) == frozenset()


def test_route_publishes_the_cancellation_for_the_lanes_next_prompt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A re-dispatch that says nothing new would reach the same collision (D6).

    The record travels the found-info channel, so ``build_bundle`` folds it into
    the lane's next prompt — and it names the lane that landed, which is what the
    agent needs to re-apply its intent somewhere else.
    """
    seen = _patch_preempt_pass(
        monkeypatch,
        conflicts={"epic.2": ("src/shared.py",)},
        changed=("src/shared.py",),
    )
    outcomes = (_executed_outcome("epic.1"), _executed_outcome("epic.2"))

    supervise.route_outcomes(tmp_path, _session(*(_lane(o.issue_id) for o in outcomes)), outcomes)

    assert len(seen["info"]) == 1
    issue, info = seen["info"][0]
    assert issue == "epic.2"
    assert info.kind == "coupling"
    assert info.affects == ("epic.2",)
    assert "epic.1" in info.summary


def test_route_cancels_a_lane_at_the_rework_cap_onto_the_decision_queue(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Re-dispatching forever is not an option: at the cap a human disposes of it."""
    seen = _patch_preempt_pass(
        monkeypatch,
        conflicts={"epic.2": ("src/shared.py",)},
        changed=("src/shared.py",),
        known_rework=1,  # one dispatch already spent earlier in the session
    )
    outcomes = (_executed_outcome("epic.1"), _executed_outcome("epic.2"))

    routed = supervise.route_outcomes(
        tmp_path, _session(*(_lane(o.issue_id) for o in outcomes)), outcomes
    )

    assert [r.route for r in routed] == ["merged", "decision"]
    assert "escalated as epic.2#abc" in routed[1].detail
    escalations = [(i, k) for i, k, _q in seen["enqueued"] if k == "escalation"]
    assert escalations == [("epic.2", "escalation")]


def test_route_does_not_cancel_a_lane_whose_merge_is_still_clean(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Overlap is not a collision: work that would land clean may not be thrown away.

    Two lanes can both own a file and touch disjoint hunks. Cancelling on scope
    overlap alone would spend a whole agent run replacing a landing that was
    about to succeed, so the trigger is the merge probe, not the declaration.
    """
    seen = _patch_preempt_pass(monkeypatch, conflicts={}, changed=("src/shared.py",))
    outcomes = (_executed_outcome("epic.1"), _executed_outcome("epic.2"))

    routed = supervise.route_outcomes(
        tmp_path, _session(*(_lane(o.issue_id) for o in outcomes)), outcomes
    )

    assert [r.route for r in routed] == ["merged", "merged"]
    assert seen["landed"] == ["epic.1", "epic.2"] and seen["info"] == []


def test_route_does_not_cancel_a_lane_over_engine_owned_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Every landing rewrites the tracker; blaming it would cancel every lane."""
    seen = _patch_preempt_pass(
        monkeypatch,
        conflicts={"epic.2": (".beads/issues.jsonl",)},
        changed=(".beads/issues.jsonl",),
    )
    outcomes = (_executed_outcome("epic.1"), _executed_outcome("epic.2"))

    routed = supervise.route_outcomes(
        tmp_path, _session(*(_lane(o.issue_id) for o in outcomes)), outcomes
    )

    # Unattributable, so the lane takes its normal landing and the bounce path
    # owns whatever it finds there.
    assert [r.route for r in routed] == ["merged", "merged"]
    assert seen["landed"] == ["epic.1", "epic.2"] and seen["rework"] == []


def test_route_does_not_cancel_a_lane_no_landing_this_pass_can_explain(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A branch that conflicts on its own is the bounce path's business, not this one.

    Cancelling costs a dispatch, so it is spent only where a landing this pass is
    demonstrably the cause; otherwise the landing runs and its rework attempt and
    coupling edge are the loop's to record.
    """
    seen = _patch_preempt_pass(
        monkeypatch,
        conflicts={"epic.2": ("src/elsewhere.py",)},
        changed=("src/shared.py",),
    )
    outcomes = (_executed_outcome("epic.1"), _executed_outcome("epic.2"))

    routed = supervise.route_outcomes(
        tmp_path, _session(*(_lane(o.issue_id) for o in outcomes)), outcomes
    )

    assert [r.route for r in routed] == ["merged", "merged"]
    assert seen["landed"] == ["epic.1", "epic.2"] and seen["info"] == []


def test_route_does_not_cancel_before_anything_has_landed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """With no landing to blame the probe is not even run — the first lane just lands."""
    seen = _patch_preempt_pass(
        monkeypatch,
        conflicts={"epic.1": ("src/shared.py",)},
        changed=("src/shared.py",),
    )
    probed: list[str] = []
    monkeypatch.setattr(
        supervise.merge,
        "probe_merge",
        lambda _r, _base, branch: probed.append(branch) or merge.ProbeResult(True, ()),
    )

    routed = supervise.route_outcomes(
        tmp_path, _session(_lane("epic.1")), (_executed_outcome("epic.1"),)
    )

    assert [r.route for r in routed] == ["merged"] and seen["landed"] == ["epic.1"]
    assert probed == []


def test_route_cancels_a_carried_lane_the_landing_broke(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A lane held last pass is no safer: its commits sit on the base that moved."""
    seen = _patch_preempt_pass(
        monkeypatch,
        conflicts={"epic.2": ("src/shared.py",)},
        changed=("src/shared.py",),
    )
    monkeypatch.setattr(
        supervise, "ready_lanes", lambda _r, _s, **_k: (_lane("epic.1"), _lane("epic.2"))
    )
    # The carried lane is prepended to the pass, so sort the landing order to put
    # the landing that breaks it first — the situation under test.
    monkeypatch.setattr(
        supervise, "_landing_order", lambda _r, outs: sorted(outs, key=lambda o: o.issue_id)
    )

    routed = supervise.route_outcomes(
        tmp_path,
        _session(_lane("epic.1"), _lane("epic.2")),
        (_executed_outcome("epic.1"),),
        carried=("epic.2",),
    )

    assert [r.route for r in routed] == ["merged", "re-dispatch"]
    assert seen["landed"] == ["epic.1"]
    assert [issue for issue, _info in seen["info"]] == ["epic.2"]


def test_route_does_not_cancel_a_lane_whose_worktree_is_gone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No worktree record, no probe: the landing decides, as it did before this."""
    seen = _patch_preempt_pass(
        monkeypatch,
        conflicts={"epic.2": ("src/shared.py",)},
        changed=("src/shared.py",),
    )
    monkeypatch.setattr(supervise.worktree, "load_session", lambda *_a, **_k: None)
    outcomes = (_executed_outcome("epic.1"), _executed_outcome("epic.2"))

    routed = supervise.route_outcomes(
        tmp_path, _session(*(_lane(o.issue_id) for o in outcomes)), outcomes
    )

    assert [r.route for r in routed] == ["merged", "merged"] and seen["info"] == []


def test_landing_order_sorts_the_pass_by_dependency(monkeypatch: pytest.MonkeyPatch) -> None:
    """The pass lands a lane before the lanes that depend on it (kjc5.20).

    Unblocked lanes go first in arrival order, blocked ones follow once their
    dependency is placed — a valid dependency order, not the arrival order.
    """
    deps = {"epic.1": frozenset({"epic.2"}), "epic.2": frozenset(), "epic.3": frozenset()}
    monkeypatch.setattr(supervise.merge, "blocking_dependencies", lambda _r, bead: deps[bead])
    outcomes = tuple(_executed_outcome(f"epic.{n}") for n in (1, 2, 3))

    ordered = [o.issue_id for o in supervise._landing_order(Path(), outcomes)]

    assert ordered.index("epic.2") < ordered.index("epic.1")
    assert ordered == ["epic.2", "epic.3", "epic.1"]


# --- Carry a held lane to landing instead of re-dispatching it (kjc5.18) ------


def test_carried_forward_carries_only_the_held_lanes() -> None:
    """Held is the one route whose work is done and merely unlanded."""
    routed = (
        supervise.RoutedOutcome("epic.1", "merged", ""),
        supervise.RoutedOutcome("epic.2", "held", ""),
        supervise.RoutedOutcome("epic.3", "rework", ""),
        supervise.RoutedOutcome("epic.4", "bounced", ""),
        supervise.RoutedOutcome("epic.5", "retry", ""),
        supervise.RoutedOutcome("epic.6", "re-dispatch", ""),
    )

    assert supervise.carried_forward(routed) == frozenset({"epic.2"})


def test_dispatch_lanes_skips_a_carried_lane(monkeypatch: pytest.MonkeyPatch) -> None:
    """The carried lane's work is on its branch: no runner may be spent on it again."""
    _patch_readiness(monkeypatch, ranked=((1, "epic.1"), (2, "epic.2")))
    monkeypatch.setattr(supervise.runner, "select_runner", lambda *_a, **_k: _MANUAL_SPEC)
    dispatched: list[str] = []

    def fake_dispatch(_repo, _session, lane, _spec, _sizing, **_kw) -> supervise.LaneOutcome:
        dispatched.append(lane.issue_id)
        return _outcome(lane.issue_id)

    monkeypatch.setattr(supervise, "_dispatch_lane", fake_dispatch)

    outcomes = supervise.dispatch_lanes(
        Path(),
        _session(_lane("epic.1"), _lane("epic.2")),
        cap=2,
        skip=frozenset({"epic.1"}),
    )

    assert dispatched == ["epic.2"] and [o.issue_id for o in outcomes] == ["epic.2"]


def test_a_carried_lane_lands_first_and_without_a_dispatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The pass owes a held lane a landing, not a fresh implement run (kjc5.18)."""
    _patch_readiness(monkeypatch, ranked=((1, "epic.1"), (2, "epic.2")))
    monkeypatch.setattr(supervise, "_landing_order", lambda _r, outcomes: list(outcomes))
    monkeypatch.setattr(supervise.merge, "head_sha", lambda _r: "sha")
    monkeypatch.setattr(supervise.merge, "changed_paths", lambda _r, _before: ())
    monkeypatch.setattr(
        supervise.policy,
        "approve_checkpoint_guarded",
        lambda *_a, **_k: policy.ApprovalResult("challenge", code="abc"),
    )
    monkeypatch.setattr(
        supervise.decisions,
        "enqueue",
        lambda _r, issue, kind, *_a, **_k: decisions_item(issue, kind),
    )
    landed: list[str] = []

    def advance(_r, issue_id, **_k):
        landed.append(issue_id)
        return loop.AdvanceResult(issue_id, "build", "verify", "merged", "landed")

    monkeypatch.setattr(supervise.loop, "advance", advance)
    session = _session(_lane("epic.1"), _lane("epic.2"))

    routed = supervise.route_outcomes(
        tmp_path, session, (_executed_outcome("epic.2"),), carried=frozenset({"epic.1"})
    )

    # epic.1 landed without ever being dispatched, and ahead of this pass's run.
    assert landed == ["epic.1", "epic.2"]
    assert [(r.issue_id, r.route) for r in routed] == [("epic.1", "merged"), ("epic.2", "merged")]


def test_a_carried_lane_that_fails_to_land_is_not_carried_again(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A red landing means the lane's own work needs changing, so dispatch resumes."""
    _patch_readiness(monkeypatch, ranked=((1, "epic.1"),))
    monkeypatch.setattr(supervise, "_landing_order", lambda _r, outcomes: list(outcomes))
    monkeypatch.setattr(
        supervise.loop,
        "advance",
        lambda _r, issue_id, **_k: _blocked_landing(issue_id, "verify-failed"),
    )

    routed = supervise.route_outcomes(
        tmp_path, _session(_lane("epic.1")), (), carried=frozenset({"epic.1"})
    )

    assert [r.route for r in routed] == ["rework"]
    assert supervise.carried_forward(routed) == frozenset()


def test_a_carried_lane_no_longer_ready_is_dropped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A lane that landed or blocked since the carry must not be landed twice."""
    _patch_readiness(monkeypatch, ranked=((1, "epic.1"),))
    monkeypatch.setattr(supervise, "_phase_of", lambda _r, _i: "verify")  # already landed
    monkeypatch.setattr(
        supervise.loop, "advance", lambda *_a, **_k: pytest.fail("carried lane was re-landed")
    )

    routed = supervise.route_outcomes(
        tmp_path, _session(_lane("epic.1")), (), carried=frozenset({"epic.1"})
    )

    assert routed == ()


def test_advance_parked_ships_a_verify_lane_without_a_runner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An approved parked lane advances through the engine; no fresh dispatch."""
    phases = {"epic.1": "verify", "epic.2": "build"}
    monkeypatch.setattr(supervise, "_phase_of", lambda _r, issue: phases[issue])
    monkeypatch.setattr(supervise, "_has_subtasks", lambda _r, _i: False)
    monkeypatch.setattr(supervise.decisions, "has_pending", lambda _r, _issue: False)
    advanced: list[str] = []

    def fake_run_until_blocked(_r, issue_id, **_k):
        advanced.append(issue_id)
        return [loop.AdvanceResult(issue_id, "ship", "done", "tore-down", "closed")]

    monkeypatch.setattr(supervise.loop, "run_until_blocked", fake_run_until_blocked)

    routed = supervise.advance_parked(tmp_path, _session(_lane("epic.1"), _lane("epic.2")))

    assert advanced == ["epic.1"]  # the build lane is dispatch's business
    assert [r.route for r in routed] == ["shipped"]


def test_ready_lanes_skip_a_lane_with_subtask_beads(monkeypatch: pytest.MonkeyPatch) -> None:
    """A mini-loop lane is driven by loop.advance, never by a top-level dispatch (D7)."""
    _patch_readiness(monkeypatch, ranked=((1, "epic.1"), (2, "epic.2")))
    monkeypatch.setattr(supervise, "_has_subtasks", lambda _r, issue: issue == "epic.1")

    ready = supervise.ready_lanes(Path(), _session(_lane("epic.1"), _lane("epic.2")))

    assert [lane.issue_id for lane in ready] == ["epic.2"]


def test_has_subtasks_counts_closed_subtasks_too(monkeypatch: pytest.MonkeyPatch) -> None:
    """A lane whose sub-tasks all closed is waiting to integrate, not to be re-run."""
    issues = {
        "epic.1": _issue("epic.1", children=(("epic.1.1", "closed"),)),
        "epic.2": _issue("epic.2"),
    }
    monkeypatch.setattr(supervise, "_run_br", _FakeBrShow(issues))
    assert supervise._has_subtasks(Path(), "epic.1") is True
    assert supervise._has_subtasks(Path(), "epic.2") is False


def test_advance_parked_drives_a_mini_loop_lane(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A build-phase lane with sub-tasks advances here; a closed sub-task keeps the loop going."""
    monkeypatch.setattr(supervise, "_phase_of", lambda _r, _i: "build")
    monkeypatch.setattr(supervise, "_has_subtasks", lambda _r, _i: True)
    monkeypatch.setattr(supervise.decisions, "has_pending", lambda _r, _i: False)
    monkeypatch.setattr(
        supervise.loop,
        "run_until_blocked",
        lambda _r, issue_id, **_k: [
            loop.AdvanceResult(issue_id, "build", "build", "sub-task", "1/2 closed"),
            loop.AdvanceResult(issue_id, "build", "build", "blocked", "awaiting the agent's work"),
        ],
    )

    routed = supervise.advance_parked(tmp_path, _session(_lane("epic.1")))

    assert [r.route for r in routed] == ["lane-step"]
    assert supervise.should_continue(routed)


def test_advance_parked_stops_on_a_mini_loop_lane_that_made_no_progress(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A lane blocked on its agent ends the pass like a handoff — it is not retriable."""
    monkeypatch.setattr(supervise, "_phase_of", lambda _r, _i: "build")
    monkeypatch.setattr(supervise, "_has_subtasks", lambda _r, _i: True)
    monkeypatch.setattr(supervise.decisions, "has_pending", lambda _r, _i: False)
    monkeypatch.setattr(
        supervise.loop,
        "run_until_blocked",
        lambda _r, issue_id, **_k: [
            loop.AdvanceResult(issue_id, "build", "build", "blocked", "awaiting the agent's work")
        ],
    )

    routed = supervise.advance_parked(tmp_path, _session(_lane("epic.1")))

    assert [r.route for r in routed] == ["lane-blocked"]
    assert not supervise.should_continue(routed)


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


# --- Client attach: observe (basicly-kjc5.8, design 7.3 layer 3) --------------


def _attach_br(monkeypatch: pytest.MonkeyPatch, fake: _FakeBr) -> None:
    """Route every module observe() reads br through to one fake.

    Each module owns its own ``_run_br`` alias — that alias is the test seam, so
    the fake has to be installed per module rather than in one shared place.
    """
    for module in (supervise, policy, decisions, loop_state):
        monkeypatch.setattr(module, "_run_br", fake)


def _grant_comment(level: str, budget: int | None = None) -> str:
    """A durable grant marker as ``policy`` records and re-reads it."""
    text = f"{policy.MARKER} grant level={level}"
    return text if budget is None else f"{text} budget={budget}"


def test_observe_reports_the_holder_lanes_decisions_and_spend(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The attach surface: who supervises, what each lane last ran, queue, grant spend."""
    br = _FakeBr(
        {
            "epic": _issue("epic", children=(("epic.1", "in_progress"), ("epic.2", "closed"))),
            "epic.1": _issue(
                "epic.1", "in_progress", external_ref="worktree:epic-1:harness/epic-1"
            ),
            "epic.2": _issue("epic.2", "closed"),
        },
        comments={"epic": [_grant_comment("L2", 5000)]},
    )
    _attach_br(monkeypatch, br)
    _fake_sessions(monkeypatch, {"epic-1"})
    run_record.record(
        tmp_path,
        "epic.1",
        run_record.build_record(
            agent="claude",
            handoff=False,
            returncode=0,
            duration_s=12.5,
            command=("claude", "-p", "<prompt>"),
            tokens=1200,
        ),
    )
    queued = decisions.enqueue(tmp_path, "epic.1", "validate", "ship without the migration?")
    # A wait the lane already served out, recorded as its own evidence (kjc5.51).
    monkeypatch.setattr(policy, "_now", lambda: 1_800.0)
    policy.record_wait(
        tmp_path,
        "epic.1",
        wait_id="epic.1#wait-ship",
        kind="checkpoint",
        subject="ship",
        requested_at="1970-01-01T00:00:00Z",
        by=policy.HUMAN_BY,
        delegated=False,
    )
    supervise.acquire(tmp_path, "epic:live", "epic")

    view = supervise.observe(tmp_path, "epic")

    assert view.root_issue == "epic"
    assert (view.children_total, view.children_open) == (2, 1)
    assert view.done is False
    assert view.holder is not None
    assert view.holder.session_id == "epic:live"
    assert (view.holder_on_this_root, view.holder_stale, view.supervised) == (True, False, True)
    assert view.lanes == (
        supervise.LaneView(
            issue_id="epic.1",
            status="in_progress",
            worktree="epic-1",
            branch="harness/epic-1",
            live=True,
            last_agent="claude",
            last_outcome=run_record.EXECUTED,
            last_run_at=view.lanes[0].last_run_at,  # stamped at record time
            last_tokens=1200,
        ),
    )
    assert [item.decision_id for item in view.pending_decisions] == [queued.decision_id]
    assert (view.grant_level, view.token_budget, view.spent_tokens) == ("L2", 5000, 1200)
    # Where the wall clock went, reported beside the compute and never folded in.
    assert (view.human_wait_s, view.delegated_wait_s, view.dispatch_s) == (1_800, 0, 12.5)


def test_observe_an_unsupervised_root_is_a_valid_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No lock, no grant, no lanes is a state to report — not an error to raise."""
    br = _FakeBr({"task": _issue("task")})
    _attach_br(monkeypatch, br)
    _fake_sessions(monkeypatch, set())

    view = supervise.observe(tmp_path, "task")

    assert view.holder is None
    assert view.supervised is False
    assert view.lanes == ()
    assert view.pending_decisions == ()
    assert (view.grant_level, view.token_budget, view.spent_tokens) == (None, None, 0)


def test_observe_flags_a_stale_holder_as_not_supervising(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A crashed holder still shows up — the client must tell it apart from working."""
    br = _FakeBr({"epic": _issue("epic")})
    _attach_br(monkeypatch, br)
    _fake_sessions(monkeypatch, set())
    lock = supervise.acquire(tmp_path, "epic:crashed", "epic")
    _backdate(lock, STALE_AFTER_S + 1)

    view = supervise.observe(tmp_path, "epic")

    assert view.holder is not None
    assert view.holder.session_id == "epic:crashed"
    assert (view.holder_stale, view.holder_on_this_root, view.supervised) == (True, True, False)


def test_observe_flags_a_holder_bound_to_another_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The lock is a repo singleton: a live holder may be supervising someone else."""
    br = _FakeBr({"epic": _issue("epic")})
    _attach_br(monkeypatch, br)
    _fake_sessions(monkeypatch, set())
    supervise.acquire(tmp_path, "other:live", "other-epic")

    view = supervise.observe(tmp_path, "epic")

    assert view.holder is not None
    assert view.holder.root_issue == "other-epic"
    assert (view.holder_on_this_root, view.holder_stale, view.supervised) == (False, False, False)


def test_observe_never_touches_the_lock(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Attaching is a pure read: it must not beat, claim, or age the holder's lock."""
    br = _FakeBr({"epic": _issue("epic")})
    _attach_br(monkeypatch, br)
    _fake_sessions(monkeypatch, set())
    lock = supervise.acquire(tmp_path, "epic:live", "epic")
    before = (lock.read_text(encoding="utf-8"), lock.stat().st_mtime)

    supervise.observe(tmp_path, "epic")

    assert (lock.read_text(encoding="utf-8"), lock.stat().st_mtime) == before
    # And observing an unlocked repo does not create one, so the next supervisor
    # to start is never refused by its own client.
    lock.unlink()
    supervise.observe(tmp_path, "epic")
    assert not lock.exists()


# --- D3 dispatch admission: the spend ceiling (basicly-kjc5.23) ---------------


def _granted(level: str, budget: int | None, spent: int) -> policy.SpendStatus:
    """A SpendStatus as policy.spend_status derives it for this grant and spend."""
    halted = budget is not None and spent >= budget
    return policy.SpendStatus(
        grant=policy.Grant(level=level, token_budget=budget),
        spent_tokens=spent,
        halted=halted,
        detail=f"{level} grant token_budget spent ({spent}/{budget} tokens)" if halted else "",
    )


def test_dispatch_lanes_halts_when_the_grant_budget_is_spent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D3: a spent budget starts no new lane runner, however ready the lanes are."""
    _patch_readiness(monkeypatch, ranked=((1, "epic.1"),))
    monkeypatch.setattr(supervise.runner, "select_runner", lambda *_a, **_k: _MANUAL_SPEC)
    monkeypatch.setattr(
        supervise,
        "_dispatch_lane",
        lambda *_a, **_k: pytest.fail("must not dispatch past the spend ceiling"),
    )
    queued: list[tuple[str, str]] = []

    def fake_enqueue(
        _repo: Path, issue: str, kind: str, question: str, detail: str = "", **_kw: object
    ) -> decisions.DecisionItem:
        queued.append((issue, kind))
        return decisions.DecisionItem("epic#h", issue, kind, question, detail)

    monkeypatch.setattr(supervise.decisions, "enqueue", fake_enqueue)

    outcomes = supervise.dispatch_lanes(
        Path(), _session(_lane("epic.1")), admission=_granted("L2", 5000, 5000)
    )

    assert outcomes == ()
    # And the halt is surfaced, or the pass reads as an ordinary idle one.
    assert queued == [("epic", "escalation")]


def test_dispatch_lanes_admits_a_grant_still_inside_its_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Under budget is not halted — the ceiling must not stop a funded session.

    Scoped to the *retrospective* half: the forward pass-spend bound is pinned small
    so this cannot be reading the other gate's verdict. Left unpinned, the assumed
    bound for an unsizeable lane is read from this repo's real run records and dwarfs
    any budget a unit test would name (basicly-vz78).
    """
    _patch_readiness(monkeypatch, ranked=((1, "epic.1"),))
    monkeypatch.setattr(supervise.runner, "select_runner", lambda *_a, **_k: _MANUAL_SPEC)
    monkeypatch.setattr(supervise.decompose, "unsized_lane_tokens", lambda *_a: (10, "measured"))
    monkeypatch.setattr(
        supervise, "_dispatch_lane", lambda _r, _s, lane, *_a, **_kw: _outcome(lane.issue_id)
    )
    monkeypatch.setattr(
        supervise.decisions,
        "enqueue",
        lambda *_a, **_k: pytest.fail("nothing to escalate while inside the budget"),
    )

    outcomes = supervise.dispatch_lanes(
        Path(), _session(_lane("epic.1")), admission=_granted("L2", 5000, 4000)
    )

    assert [o.issue_id for o in outcomes] == ["epic.1"]


def test_dispatch_lanes_reads_the_ceiling_when_no_admission_is_passed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Omitting *admission* must re-read it, or a caller bypasses D3 by forgetting."""
    _patch_readiness(monkeypatch, ranked=((1, "epic.1"),))
    monkeypatch.setattr(supervise.runner, "select_runner", lambda *_a, **_k: _MANUAL_SPEC)
    monkeypatch.setattr(
        supervise.policy, "spend_status", lambda *_a, **_k: _granted("L3", 100, 100)
    )
    monkeypatch.setattr(
        supervise,
        "_dispatch_lane",
        lambda *_a, **_k: pytest.fail("must not dispatch past the spend ceiling"),
    )
    monkeypatch.setattr(supervise.decisions, "enqueue", lambda *_a, **_k: None)

    assert supervise.dispatch_lanes(Path(), _session(_lane("epic.1"))) == ()


def test_dispatch_halt_is_one_idempotent_queue_item(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Every halted pass re-surfaces the same item, not a fresh notification each time."""
    br = _FakeBr({"epic": _issue("epic")})
    monkeypatch.setattr(decisions, "_run_br", br)
    monkeypatch.setattr(loop_state, "_run_br", br)
    monkeypatch.setattr(decisions, "_notify", lambda *_a, **_k: None)
    admission = _granted("L2", 5000, 6000)

    first = supervise.record_dispatch_halt(tmp_path, "epic", admission)
    second = supervise.record_dispatch_halt(tmp_path, "epic", admission)

    assert first.decision_id == second.decision_id
    assert first.kind == "escalation"
    assert len(br.comments["epic"]) == 1


def test_an_unmeterable_halt_asks_its_own_question(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A ceiling that cannot bind is a different ask from one that is spent (jr0l.35).

    Items are keyed by (issue, kind, question), so sharing the budget wording would
    also fold the two halts into one item and hide whichever arrived second.
    """
    br = _FakeBr({"epic": _issue("epic")})
    monkeypatch.setattr(decisions, "_run_br", br)
    monkeypatch.setattr(loop_state, "_run_br", br)
    monkeypatch.setattr(decisions, "_notify", lambda *_a, **_k: None)
    spent = _granted("L2", 5000, 6000)
    unmetered = policy.SpendStatus(
        grant=policy.Grant(level="L2", token_budget=5000),
        spent_tokens=0,
        halted=True,
        detail="L2 grant cannot be metered: 1 dispatch(es) under it reported no measurable usage",
        unmetered_dispatches=1,
    )

    budget_item = supervise.record_dispatch_halt(tmp_path, "epic", spent)
    unmetered_item = supervise.record_dispatch_halt(tmp_path, "epic", unmetered)

    assert budget_item.decision_id != unmetered_item.decision_id
    assert "no measurable usage" in unmetered_item.question
    assert len(br.comments["epic"]) == 2


# --- Autonomous delegation: the decider in the pass (basicly-kjc5.40) ---------


def _delegation_env(
    monkeypatch: pytest.MonkeyPatch,
    *,
    pending: tuple[decisions.DecisionItem, ...],
    outcomes: dict[str, object],
) -> list[str]:
    """Stub the queue read and the decider, recording which ids were offered."""
    offered: list[str] = []
    monkeypatch.setattr(supervise.decisions, "pending", lambda *_a, **_k: pending)

    def fake_invoke(_repo: Path, decision_id: str, _root: str, **_kw: object) -> object:
        offered.append(decision_id)
        return outcomes[decision_id]

    monkeypatch.setattr(supervise.decisions, "invoke_decider", fake_invoke)
    return offered


def _item(decision_id: str, kind: str, *, answer: str | None = None) -> decisions.DecisionItem:
    return decisions.DecisionItem(
        decision_id=decision_id,
        issue_id=decision_id.split("#", maxsplit=1)[0],
        kind=kind,
        question=f"{kind}?",
        answer=answer,
        answered_by="decider:fake" if answer else None,
    )


def test_delegate_decisions_answers_the_delegable_kinds(monkeypatch: pytest.MonkeyPatch) -> None:
    """The autonomous path: an L2 grant hands needs-input and escalation to the decider."""
    needs = _item("epic.1#a", "needs-input")
    esc = _item("epic.1#b", "escalation")
    offered = _delegation_env(
        monkeypatch,
        pending=(needs, esc),
        outcomes={
            "epic.1#a": _item("epic.1#a", "needs-input", answer="postgres"),
            "epic.1#b": _item("epic.1#b", "escalation", answer="retry"),
        },
    )

    delegated = supervise.delegate_decisions(
        Path(), _session(_lane("epic.1")), admission=_granted("L2", 5000, 10)
    )

    assert offered == ["epic.1#a", "epic.1#b"]
    assert [(d.decision_id, d.answered) for d in delegated] == [
        ("epic.1#a", True),
        ("epic.1#b", True),
    ]
    assert "postgres" in delegated[0].detail


def test_delegate_decisions_never_offers_a_checkpoint_validate_or_stall(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Three kinds stay human-only, and each for its own reason.

    A ``checkpoint`` item exists *because* the grant already refused the
    approval, so answering it would clear the lane's hold with the checkpoint
    still unapproved — around D3's ladder. ``validate`` re-judged by an agent is
    the consensus-voting shape D9 rejects, and ``stall`` is a fact about a killed
    process rather than a corpus question.
    """
    kinds = ("checkpoint", "validate", "stall")
    items = tuple(_item(f"epic.1#{n}", kind) for n, kind in enumerate(kinds))
    offered = _delegation_env(monkeypatch, pending=items, outcomes={})

    delegated = supervise.delegate_decisions(
        Path(), _session(_lane("epic.1")), admission=_granted("L3", 5000, 10)
    )

    assert offered == []
    assert delegated == ()


@pytest.mark.parametrize("level", ["L0", "L1"])
def test_delegate_decisions_needs_an_l2_grant(monkeypatch: pytest.MonkeyPatch, level: str) -> None:
    """Below L2 nothing delegates: L0 is task-by-task and L1 only pre-approves decompose."""
    offered = _delegation_env(monkeypatch, pending=(_item("epic.1#a", "needs-input"),), outcomes={})

    assert (
        supervise.delegate_decisions(
            Path(), _session(_lane("epic.1")), admission=_granted(level, None, 0)
        )
        == ()
    )
    assert offered == []


def test_delegate_decisions_needs_any_grant_at_all(monkeypatch: pytest.MonkeyPatch) -> None:
    """An ungranted session is human-driven; the decider must not run unasked."""
    offered = _delegation_env(monkeypatch, pending=(_item("epic.1#a", "needs-input"),), outcomes={})

    assert (
        supervise.delegate_decisions(Path(), _session(_lane("epic.1")), admission=_UNGRANTED) == ()
    )
    assert offered == []


def test_delegate_decisions_stops_at_the_spend_ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    """D3 halts delegated decisions on the same ceiling as dispatch (basicly-kjc5.23)."""
    offered = _delegation_env(monkeypatch, pending=(_item("epic.1#a", "needs-input"),), outcomes={})

    assert (
        supervise.delegate_decisions(
            Path(), _session(_lane("epic.1")), admission=_granted("L2", 100, 100)
        )
        == ()
    )
    assert offered == []


def test_delegate_decisions_reports_an_abstention_as_still_the_humans(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An abstention is the correct outcome, reported as such - never retried around."""
    _delegation_env(
        monkeypatch,
        pending=(_item("epic.1#a", "needs-input"),),
        outcomes={"epic.1#a": decisions.DeciderVerdict("", "not in the corpus", 0.0, abstain=True)},
    )

    delegated = supervise.delegate_decisions(
        Path(), _session(_lane("epic.1")), admission=_granted("L2", 5000, 10)
    )

    assert [(d.answered, d.detail) for d in delegated] == [(False, "not in the corpus")]


def test_delegate_decisions_contains_one_broken_invocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One failed delegation must not strand every other decision in the pass."""
    first, second = _item("epic.1#a", "needs-input"), _item("epic.2#b", "needs-input")
    monkeypatch.setattr(supervise.decisions, "pending", lambda *_a, **_k: (first, second))

    def flaky(_repo: Path, decision_id: str, _root: str, **_kw: object) -> object:
        if decision_id == "epic.1#a":
            raise RuntimeError("tracker locked")
        return _item(decision_id, "needs-input", answer="mysql")

    monkeypatch.setattr(supervise.decisions, "invoke_decider", flaky)

    delegated = supervise.delegate_decisions(
        Path(), _session(_lane("epic.1")), admission=_granted("L2", 5000, 10)
    )

    assert [(d.decision_id, d.answered) for d in delegated] == [
        ("epic.1#a", False),
        ("epic.2#b", True),
    ]
    assert "tracker locked" in delegated[0].detail


def test_delegate_decisions_beats_the_lock_between_invocations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A slow decider must not let the singleton lock go stale mid-delegation."""
    items = (_item("epic.1#a", "needs-input"), _item("epic.2#b", "needs-input"))
    _delegation_env(
        monkeypatch,
        pending=items,
        outcomes={i.decision_id: _item(i.decision_id, i.kind, answer="x") for i in items},
    )
    beats = {"n": 0}

    def beat() -> None:
        beats["n"] += 1

    supervise.delegate_decisions(
        Path(), _session(_lane("epic.1")), beat=beat, admission=_granted("L2", 5000, 10)
    )

    assert beats["n"] == 2


# --- Answered decisions reach the lane's next dispatch (basicly-kjc5.40) ------


def test_build_bundle_folds_the_lanes_answered_questions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An answer that never reaches the prompt is an answer nobody acts on.

    Without this the lane re-dispatches with the prompt it already had and
    re-blocks on the same fact, so both the human and the decider path deliver
    their answer to nobody.
    """
    br = _FakeBr({"epic.1": _issue("epic.1")})
    _install_br(monkeypatch, br)
    monkeypatch.setattr(decisions, "_notify", lambda *_a, **_k: None)
    item = decisions.enqueue(Path(), "epic.1", "needs-input", "which db?")
    decisions.answer(Path(), item.decision_id, "postgres", by="human")
    still_open = decisions.enqueue(Path(), "epic.1", "escalation", "retry or park?")

    bundle = supervise.build_bundle(Path(), "epic.1")

    assert [i.decision_id for i in bundle.answers] == [item.decision_id]
    assert "which db? → postgres (answered by human)" in bundle.prompt
    assert "do not re-ask" in bundle.prompt
    # An unanswered item is not folded: it is the question, not an answer.
    assert still_open.question not in bundle.prompt


def test_build_bundle_omits_the_answers_section_when_there_are_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A lane that never blocked gets the plain dispatch prompt, unchanged."""
    br = _FakeBr({"epic.1": _issue("epic.1")})
    _install_br(monkeypatch, br)

    bundle = supervise.build_bundle(Path(), "epic.1")

    assert bundle.answers == ()
    assert bundle.prompt == loop.dispatch_prompt("epic.1")


# --- Stall flagging on a lane dispatch (basicly-kjc5.25, design section 6) -----


def test_lane_activity_changes_on_a_commit_and_on_a_file_write(tmp_path: Path) -> None:
    """The probe tracks the two things a working lane changes: commits and files."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "t"], check=True)
    (tmp_path / "a.txt").write_text("one", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "first"], check=True)

    idle = supervise.lane_activity(tmp_path)
    assert supervise.lane_activity(tmp_path) == idle  # stable while nothing happens

    (tmp_path / "b.txt").write_text("two", encoding="utf-8")
    wrote = supervise.lane_activity(tmp_path)
    assert wrote != idle  # an uncommitted write is progress

    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "second"], check=True)
    assert supervise.lane_activity(tmp_path) not in (idle, wrote)  # so is the commit


def test_lane_activity_never_raises_on_a_path_that_is_not_a_repo(tmp_path: Path) -> None:
    """The probe observes a dispatch; a bad path must not break the one it watches."""
    assert supervise.lane_activity(tmp_path / "nope")  # a fingerprint, not an exception


def test_flag_stalled_lane_queues_one_item_and_names_the_hard_kill(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A stall is queued once per lane, and says what happens if nobody intervenes."""
    br = _FakeBr({"epic.1": _issue("epic.1"), "epic.2": _issue("epic.2")})
    _install_br(monkeypatch, br)
    monkeypatch.setattr(decisions, "_notify", lambda *_a, **_k: None)

    first = supervise.flag_stalled_lane(tmp_path, "epic.1", 900.0, 3600.0)
    again = supervise.flag_stalled_lane(tmp_path, "epic.1", 900.0, 3600.0)

    assert first.decision_id == again.decision_id
    assert len(br.comments["epic.1"]) == 1
    assert first.kind == "stall"
    assert "900s" in first.detail
    assert "3600s" in first.detail  # the run continues to the hard kill
    # A sub-second window must not render as "0s", which would say the opposite of
    # what happened (found by exercising it with a tight stall_after).
    # A different lane: the idempotence key is (issue, kind, question), and the
    # question is identical here, so re-flagging epic.1 would return the first
    # item with the first detail.
    brief = supervise.flag_stalled_lane(tmp_path, "epic.2", 0.2, 3600.0)
    assert "0.2s" in brief.detail
    assert "0s;" not in brief.detail
    assert first.pending  # it is the human's to act on


def test_a_stall_flag_is_retired_once_its_dispatch_has_ended(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The flag asks about a hard kill that can no longer arrive, so it must not linger.

    Left pending, ``has_pending`` drops the lane from ``ready_lanes`` and from the
    carry, parking a lane that was merely slow on a question with no live subject.
    """
    br = _FakeBr({"epic.1": _issue("epic.1")})
    _install_br(monkeypatch, br)
    monkeypatch.setattr(decisions, "_notify", lambda *_a, **_k: None)
    flagged = supervise.flag_stalled_lane(tmp_path, "epic.1", 900.0, 3600.0)
    assert flagged.pending
    assert decisions.has_pending(tmp_path, "epic.1")

    disposed = supervise.resolve_stall_flag(tmp_path, "epic.1")

    assert disposed == (flagged.decision_id,)
    assert not decisions.has_pending(tmp_path, "epic.1")  # the lane is dispatchable again
    # Answered, not deleted: the audit trail still shows the lane was flagged.
    item = decisions.get(tmp_path, flagged.decision_id)
    assert item is not None and not item.pending
    assert item.answered_by == decisions.ENGINE_BY


def test_retiring_a_stall_flag_is_idempotent_and_leaves_other_items_alone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """It may only retire its own moot question — never a live one.

    The hard-kill item asks something a human still has to answer, so the engine must
    walk past it. Answering that would be the engine disposing of a real decision.
    """
    br = _FakeBr({"epic.1": _issue("epic.1")})
    _install_br(monkeypatch, br)
    monkeypatch.setattr(decisions, "_notify", lambda *_a, **_k: None)
    supervise.flag_stalled_lane(tmp_path, "epic.1", 900.0, 3600.0)
    hard_kill = decisions.enqueue(
        tmp_path, "epic.1", "stall", "runner claude hit runner_timeout (3600s): retry?"
    )

    assert len(supervise.resolve_stall_flag(tmp_path, "epic.1")) == 1
    assert supervise.resolve_stall_flag(tmp_path, "epic.1") == ()  # nothing left to retire

    still_open = decisions.get(tmp_path, hard_kill.decision_id)
    assert still_open is not None and still_open.pending


def test_an_engine_retired_stall_flag_is_not_charged_as_human_wait(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No human waited on it, so counting the interval would overstate human wait (D11)."""
    br = _FakeBr({"epic.1": _issue("epic.1")})
    _install_br(monkeypatch, br)
    monkeypatch.setattr(decisions, "_notify", lambda *_a, **_k: None)
    recorded: list = []
    monkeypatch.setattr(
        policy, "record_wait", lambda *_a, **kwargs: recorded.append(kwargs) or None
    )
    supervise.flag_stalled_lane(tmp_path, "epic.1", 900.0, 3600.0)

    supervise.resolve_stall_flag(tmp_path, "epic.1")

    assert len(recorded) == 1
    assert recorded[0]["delegated"] is True
    assert recorded[0]["by"] == decisions.ENGINE_BY


def test_stalled_lane_is_flagged_while_the_dispatch_still_completes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The acceptance criterion: flag a wedged lane, then let the run finish.

    A stall must never shorten a dispatch — ``runner_timeout`` stays the only
    terminal action, so a slow-but-working run is not killed early.
    """
    br = _FakeBr({"epic.1": _issue("epic.1")})
    _install_br(monkeypatch, br)
    monkeypatch.setattr(decisions, "_notify", lambda *_a, **_k: None)
    monkeypatch.setattr(
        supervise,
        "load_runner_config",
        lambda _r: RunnerConfig(
            specs=(_MANUAL_SPEC,), default="manual", stall_after=0.08, runner_timeout=3600.0
        ),
    )
    monkeypatch.setattr(
        supervise, "build_bundle", lambda *_a, **_k: supervise.DispatchBundle("epic.1", "p", ())
    )

    class _WtSession:
        worktree_path = str(tmp_path)

    monkeypatch.setattr(supervise.worktree, "load_session", lambda *_a, **_k: _WtSession())
    monkeypatch.setattr(supervise.loop, "record_run", lambda *_a, **_k: None)
    # The band gate reads the estimator before the dispatch (basicly-jr0l.16), and
    # `decompose` keeps its own br alias — left live this timing test would spawn a
    # real br and contend on its machine-global lock. In-band, so the lane dispatches.
    monkeypatch.setattr(
        supervise.decompose,
        "resolve_dispatch_sizing",
        lambda *_a: _lookup(_dispatch_sizing(20_000)),
    )
    # A dispatch that runs long enough to be sampled and makes no progress.
    monkeypatch.setattr(supervise, "lane_activity", lambda _cwd: "frozen")

    def slow_run(*_a: object, **_k: object) -> runner.RunResult:
        time.sleep(0.45)
        return runner.RunResult(
            runner="manual", command=(), executed=True, returncode=0, stdout="done"
        )

    monkeypatch.setattr(supervise.runner, "run", slow_run)

    outcome = supervise._dispatch_lane(
        tmp_path, _session(_lane("epic.1")), _lane("epic.1"), _MANUAL_SPEC, _sizing()
    )

    # Flagged...
    stalls = [i for i in decisions.items_on(tmp_path, "epic.1") if i.kind == "stall"]
    assert len(stalls) == 1
    assert "may be stuck" in stalls[0].question
    # ...and the run was left alone to finish.
    assert outcome.result is not None
    assert outcome.result.returncode == 0
    assert outcome.result.stdout == "done"
    # ...and the flag did not outlive the dispatch. This is the held-landing case:
    # the run finished green and nothing merged it, which is exactly when the stale
    # item used to park the lane on a hard kill that could no longer arrive
    # (basicly-jr0l.52). The record survives; only the pending question does not.
    assert not stalls[0].pending
    assert stalls[0].answered_by == decisions.ENGINE_BY
    assert not decisions.has_pending(tmp_path, "epic.1")  # the next pass dispatches it


def test_dispatch_lane_records_its_forecast_beside_its_actual(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The lane dispatch carries the forecast too (basicly-jr0l.34).

    Not an afterthought: the measured 160-420x misses were all spent on lane
    dispatches, so a forecast that reached only the loop's own dispatch would leave
    exactly the expensive runs unpairable.
    """
    codex = _codex()
    _worker_fixture(monkeypatch, tmp_path, stdout=_codex_events(50_000))
    captured: dict = {}
    monkeypatch.setattr(supervise.loop, "record_run", lambda *_a, **kw: captured.update(kw))
    monkeypatch.setattr(
        supervise.decompose,
        "resolve_dispatch_sizing",
        lambda *_a: _lookup(_dispatch_sizing(21_000, 9_000)),
    )

    supervise._dispatch_lane(tmp_path, _session(_lane("epic.1")), _lane("epic.1"), codex, _sizing())

    assert captured["forecast_tokens"] == 21_000
    assert captured["task_class"] == "task"
    assert captured["forecast_source"] == "dispatch"


def test_an_unsizeable_lane_records_the_bound_it_was_gated_on(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A lane with no readable scope still lands with a forecast half (basicly-jr0l.58).

    The pass gates such a lane at the assumed bound, but its record carried no forecast
    at all - so after a completed four-lane run ``usage forecast`` still reported "no
    dispatch carries both a forecast and a measured actual", 17 actual with no forecast.
    The telemetry needed to calibrate the bound was the one thing the bound's own
    dispatches never produced.
    """
    codex = _codex()
    _worker_fixture(monkeypatch, tmp_path, stdout=_codex_events(50_000))
    captured: dict = {}
    monkeypatch.setattr(supervise.loop, "record_run", lambda *_a, **kw: captured.update(kw))
    # No declared scope: exactly the beads the estimator declines to size.
    monkeypatch.setattr(
        supervise.decompose,
        "resolve_dispatch_sizing",
        lambda *_a: _lookup(None, decompose.SCOPE_UNDECLARED),
    )
    monkeypatch.setattr(
        supervise.decompose, "unsized_lane_tokens", lambda *_a: (16_002_352, "measured")
    )

    supervise._dispatch_lane(tmp_path, _session(_lane("epic.1")), _lane("epic.1"), codex, _sizing())

    assert captured["forecast_tokens"] == 16_002_352
    # Namespaced, so nothing can read an assumption as an estimate off a real scope.
    assert captured["forecast_source"] == "assumed:measured"


# --- The working-set band at dispatch (basicly-jr0l.16) ------------------------
#
# The D8 sizing governor refused an out-of-band decompose *plan*, so the band bound
# only work that arrived through decompose; a supervised pass over pre-existing leaf
# beads started whatever the scheduler ranked first at any size. Measured on this
# repo's own ready set at 8000..64000, the top-ranked lane estimated 108605
# working-set tokens and the supervisor would have started it without a word.


def test_admit_working_set_never_checked_a_bead_that_declares_no_scope(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The gate's own defect, through the real estimator (basicly-jr0l.60).

    Measured on the 2026-08-01 four-lane proof run, every dispatched lane came back
    ``sizing=None, violation=None, refused=False`` — a verdict indistinguishable from
    "checked and fits" — and all four then crossed the context ceiling. Deliberately
    not the stubbed lookup: the conflation lived in the estimator's answer, so only a
    real scopeless record proves it is gone.
    """
    monkeypatch.setattr(
        decompose,
        "_run_br",
        lambda _r, args, **_k: _Proc(
            json.dumps([
                {"id": args[1], "issue_type": "task", "description": "## Context\n\nhand-filed"}
            ])
        ),
    )

    admission = supervise.admit_working_set(tmp_path, "epic.1", _sizing())

    assert admission.sizing is None
    assert admission.checked is False
    assert admission.absence == decompose.SCOPE_UNDECLARED
    # Still admitted — failing closed here bans hand-filed work — but no longer silent.
    assert admission.refused is False
    assert admission.violation is not None
    assert "never checked" in admission.violation
    assert "8000..64000" in admission.violation


def test_admit_working_set_stays_indeterminate_when_the_bead_cannot_be_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A failed read is not a finding about the package, and must not read as one.

    The distinction the fix rests on: an undeclared scope is structural and re-reading
    will not change it, while a tracker failure says nothing at all about the lane's
    size — so it carries no notice and nothing to escalate.
    """

    def broken(*_a: object, **_k: object) -> _Proc:
        raise RuntimeError("br show failed")

    monkeypatch.setattr(decompose, "_run_br", broken)

    admission = supervise.admit_working_set(tmp_path, "epic.1", _sizing())

    assert admission.checked is False
    assert admission.absence == decompose.SCOPE_UNREADABLE
    assert admission.violation is None
    assert admission.refused is False


def _band_fixture(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sizing: decompose.DispatchSizing | None,
    *,
    spawn_is_a_failure: bool = False,
    absence: str = decompose.SCOPE_UNREADABLE,
) -> list[str]:
    """A lane dispatch environment whose estimator answers *sizing*.

    With *sizing* None, *absence* says which unsizeable answer it is — the two are
    admitted differently (basicly-jr0l.60).

    Returns the list every spawn appends its runner name to. With
    *spawn_is_a_failure* the spawn **raises**: a refusal is only proved by making a
    started process the failure, never by reading back a flag the test itself set.
    """
    _install_br(monkeypatch, _FakeBr({"epic.1": _issue("epic.1")}))
    monkeypatch.setattr(decisions, "_notify", lambda *_a, **_k: None)
    monkeypatch.setattr(policy, "record_wait", lambda *_a, **_k: None)
    (tmp_path / "wt").mkdir(exist_ok=True)

    class _WtSession:
        worktree_path = str(tmp_path / "wt")

    monkeypatch.setattr(supervise.worktree, "load_session", lambda *_a, **_k: _WtSession())
    monkeypatch.setattr(
        supervise, "build_bundle", lambda *_a, **_k: supervise.DispatchBundle("epic.1", "p", ())
    )
    monkeypatch.setattr(supervise.loop, "record_run", lambda *_a, **_k: None)
    monkeypatch.setattr(
        supervise.decompose, "resolve_dispatch_sizing", lambda *_a: _lookup(sizing, absence)
    )
    spawned: list[str] = []

    def spawn(spec, _prompt, _cwd, **_kw):
        spawned.append(spec.name)
        if spawn_is_a_failure:
            raise AssertionError(f"a refused lane must not start {spec.name}")
        return runner.RunResult(spec.name, (spec.name,), executed=True, returncode=0, stdout="")

    monkeypatch.setattr(supervise.runner, "run", spawn)
    return spawned


def _dispatch_epic1(tmp_path: Path) -> supervise.LaneOutcome:
    return supervise._dispatch_lane(
        tmp_path, _session(_lane("epic.1")), _lane("epic.1"), _codex(), _sizing()
    )


def test_dispatch_refuses_a_lane_above_the_working_set_ceiling(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The acceptance criterion, expensive end: no process starts, and it says why.

    Over-size is the direction that costs money — the run would overflow the very
    window it was sized against — so the band blocks rather than advises, the same
    shape as refusing a dispatch whose model tier resolves to nothing
    (basicly-kjc5.59).
    """
    spawned = _band_fixture(
        monkeypatch, tmp_path, _dispatch_sizing(100_000), spawn_is_a_failure=True
    )

    outcome = _dispatch_epic1(tmp_path)

    assert spawned == []  # nothing was started, so nothing was spent
    assert outcome.refused is True
    assert outcome.result is None
    # Both numbers the operator has to act on: the estimate and the band it broke.
    assert "100000" in outcome.detail
    assert "64000" in outcome.detail
    assert "refused" in outcome.detail
    # And it is held, not merely reported: an unanswered item drops the lane from
    # the ready set, so the next pass does not re-derive the same refusal.
    items = decisions.items_on(tmp_path, "epic.1")
    assert [(i.kind, i.pending) for i in items] == [("escalation", True)]
    assert decisions.has_pending(tmp_path, "epic.1") is True
    assert items[0].decision_id in outcome.detail


def test_dispatch_escalates_but_still_runs_a_lane_below_the_floor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The acceptance criterion, cheap end: escalated, recorded, and not wedged.

    An under-size lane succeeds; it only wastes the per-lane overhead a merge with a
    sibling would have saved. Blocking it would strand deliverable work behind a
    human answer, and a ready set of small beads would wedge a whole supervised run —
    so the advisory is recorded and retired by the engine in the same breath.
    """
    spawned = _band_fixture(monkeypatch, tmp_path, _dispatch_sizing(3_000))

    outcome = _dispatch_epic1(tmp_path)

    assert spawned == ["codex"]  # the work still ran
    assert outcome.refused is False
    assert outcome.detail == "finished; ready to land"
    items = decisions.items_on(tmp_path, "epic.1")
    assert len(items) == 1
    assert items[0].kind == "escalation"
    assert "3000" in items[0].detail  # the estimate...
    assert "8000" in items[0].detail  # ...and the band it broke
    # Retired by the engine, so it never lands in a human's wait column and never
    # holds the lane out of the next pass.
    assert items[0].answered_by == decisions.ENGINE_BY
    assert decisions.has_pending(tmp_path, "epic.1") is False


def test_dispatch_runs_an_in_band_lane_without_a_word(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The control: a lane inside the band dispatches with nothing queued.

    Without this, a gate that refused *everything* would pass every other test in
    this section.
    """
    spawned = _band_fixture(monkeypatch, tmp_path, _dispatch_sizing(20_000))

    outcome = _dispatch_epic1(tmp_path)

    assert spawned == ["codex"]
    assert outcome.refused is False
    assert decisions.items_on(tmp_path, "epic.1") == ()


def test_dispatch_admits_a_lane_whose_working_set_cannot_be_estimated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An unsizeable lane still dispatches — a missing estimate is not an out-of-band one.

    60 of this repo's 87 open beads carry no ``## Scope`` section, so failing closed
    here would turn a sizing governor into a ban on hand-filed work: strictly worse
    than the gap it closes. The model precedent refuses because a *declared* tier
    resolved to nothing; an absent scope declares nothing to contradict.
    """
    spawned = _band_fixture(monkeypatch, tmp_path, None, absence=decompose.SCOPE_UNDECLARED)

    outcome = _dispatch_epic1(tmp_path)

    assert spawned == ["codex"]
    assert outcome.refused is False


def test_dispatch_does_not_silently_admit_a_lane_that_declares_no_scope(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The acceptance criterion: unsized is not the same report as in-band.

    Admitting was only half a decision (basicly-jr0l.60). On the 2026-08-01 four-lane
    proof run every dispatched lane was unsizeable, so the band checked nothing and
    said nothing, and all four then overran the ceiling they were never compared
    against. The lane still runs — the alternative bans hand-filed work — but it now
    carries a recorded notice naming the band it was never measured against, retired
    by the engine so nothing wedges.
    """
    spawned = _band_fixture(monkeypatch, tmp_path, None, absence=decompose.SCOPE_UNDECLARED)

    outcome = _dispatch_epic1(tmp_path)

    assert spawned == ["codex"]  # the work still ran
    assert outcome.refused is False
    items = decisions.items_on(tmp_path, "epic.1")
    assert len(items) == 1
    assert items[0].kind == "escalation"
    # Its own question, not a second generation of the out-of-band one: an operator
    # reading the queue has to be able to tell "too big" from "never measured".
    assert items[0].question == supervise.UNSIZED_QUESTION
    assert "never" in items[0].detail
    assert "8000..64000" in items[0].detail  # the band nothing was compared against
    # Retired by the engine, so it never lands in a human's wait column and never
    # holds the lane out of the next pass.
    assert items[0].answered_by == decisions.ENGINE_BY
    assert decisions.has_pending(tmp_path, "epic.1") is False


def test_dispatch_admits_a_lane_whose_estimator_cannot_be_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A broken tracker read must not wedge the whole supervised run either.

    And it queues nothing: a transient br failure is a fact about the tracker, not a
    finding about the package, so filing it as a sizing notice would put a hiccup in
    the lane's audit trail (basicly-jr0l.60).
    """

    def unreadable(*_a: object) -> decompose.SizingLookup:
        raise RuntimeError("br export failed")

    spawned = _band_fixture(monkeypatch, tmp_path, None)
    monkeypatch.setattr(supervise.decompose, "resolve_dispatch_sizing", unreadable)

    assert _dispatch_epic1(tmp_path).refused is False
    assert spawned == ["codex"]
    assert decisions.items_on(tmp_path, "epic.1") == ()


def test_route_a_refused_lane_to_the_queue_rather_than_a_retry(tmp_path: Path) -> None:
    """A deterministic refusal cannot be re-run into a pass, so it must not retry.

    Every retry would reach the identical verdict and only delay the escalation that
    already holds the lane — and a retriable route would keep the standing loop
    spinning on a lane nothing can change.
    """
    refused = supervise.LaneOutcome(
        issue_id="epic.1",
        runner_name="codex",
        result=None,
        needs_fact=None,
        occupancy=None,
        overrun=False,
        followup_id=None,
        detail="dispatch refused before it started",
        refused=True,
    )

    routed = supervise.route_outcomes(tmp_path, _session(_lane("epic.1")), (refused,))

    assert [r.route for r in routed] == ["decision"]
    assert supervise.should_continue(routed) is False


# --- The spend ceiling at pass admission (basicly-jr0l.22) --------------------
#
# D3's ceiling was retrospective: it compared spend *already recorded* against the
# budget, so a pass was admitted whenever the previous ones happened to fit. The
# basicly-u6jq.1 proof run admitted a pass against a 5000000-token ceiling that then
# spent 46026602, and halted on the pass after the money was gone. These pin the
# forward half — and that it never buys the fix by interrupting a running agent.


def _forecast(tokens: int | None) -> decompose.SpendForecast:
    """A spend forecast of exactly *tokens*, with the calibration left unread.

    Only ``tokens`` is summed by the pass gate, so building a real calibration here
    would pin arithmetic these tests are not about.
    """
    return decompose.SpendForecast(
        tokens=tokens,
        cost=None,
        wall_clock_s=None,
        calibration=run_record.calibrate_spend(
            run_record.ForecastErrorReport(),
            model=None,
            task_class="task",
            min_samples=10,
            window=50,
        ),
    )


def _pass_fixture(
    monkeypatch: pytest.MonkeyPatch,
    *,
    sizings: dict[str, decompose.DispatchSizing | None],
    forecasts: dict[str, int | None],
    dispatch_is_a_failure: bool = False,
    unsized_tokens: int = 1_000,
) -> list[tuple[str, str]]:
    """A pass whose lanes size and forecast as given; returns the queue-item log.

    With *dispatch_is_a_failure* any dispatch **raises**: a refusal is only proved
    by making a started lane the failure, never by reading back a flag the test set.

    A None sizing is the *undeclared* absence, since a bead nobody decomposed is what
    an unsizeable lane really is on this tracker (basicly-jr0l.60).

    *unsized_tokens* pins the bound an unsizeable lane is counted at. Pinned rather
    than left real, because the live implementation reads this repo's own run records
    — so an unpatched test would size its lanes from whatever the last supervised run
    happened to cost, and change verdict as that history grows.
    """
    monkeypatch.setattr(supervise.runner, "select_runner", lambda *_a, **_k: _MANUAL_SPEC)
    monkeypatch.setattr(supervise, "load_sizing_config", lambda _r: _sizing())
    monkeypatch.setattr(
        supervise.decompose, "unsized_lane_tokens", lambda *_a: (unsized_tokens, "measured")
    )
    monkeypatch.setattr(
        supervise.decompose,
        "resolve_dispatch_sizing",
        lambda _r, issue_id: _lookup(sizings.get(issue_id), decompose.SCOPE_UNDECLARED),
    )

    def fake_forecasts(_repo, items, _sizing):
        # Keyed by the estimate the caller passed, so a gate that forecast the wrong
        # lane's sizing would mis-total rather than quietly pass.
        by_total = {sizing.estimate.total: issue for issue, sizing in sizings.items() if sizing}
        return tuple(_forecast(forecasts.get(by_total[item.estimate.total])) for item in items)

    monkeypatch.setattr(supervise.decompose, "dispatch_spend_forecasts", fake_forecasts)
    if dispatch_is_a_failure:
        monkeypatch.setattr(
            supervise,
            "_dispatch_lane",
            lambda *_a, **_k: pytest.fail("a refused pass must not start any lane"),
        )
    else:
        monkeypatch.setattr(
            supervise, "_dispatch_lane", lambda _r, _s, lane, *_a, **_kw: _outcome(lane.issue_id)
        )
    queued: list[tuple[str, str]] = []

    def fake_enqueue(
        _repo: Path, issue: str, kind: str, question: str, detail: str = "", **_kw: object
    ) -> decisions.DecisionItem:
        queued.append((question, detail))
        return decisions.DecisionItem("epic#p", issue, kind, question, detail)

    monkeypatch.setattr(supervise.decisions, "enqueue", fake_enqueue)
    return queued


def test_pass_is_refused_when_its_forecast_exceeds_the_remaining_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The acceptance criterion: nothing dispatches, and the message carries both numbers.

    Two lanes forecast 4000 tokens each against 5000 remaining. Neither alone
    overruns — that is exactly the shape the retrospective gate admitted, because it
    only ever compared what had already been spent.
    """
    _patch_readiness(monkeypatch, ranked=((1, "epic.1"), (2, "epic.2")))
    queued = _pass_fixture(
        monkeypatch,
        sizings={"epic.1": _dispatch_sizing(20_000), "epic.2": _dispatch_sizing(30_000)},
        forecasts={"epic.1": 4_000, "epic.2": 4_000},
        dispatch_is_a_failure=True,
    )

    outcomes = supervise.dispatch_lanes(
        Path(),
        _session(_lane("epic.1"), _lane("epic.2")),
        admission=_granted("L3", 10_000, 5_000),
    )

    assert outcomes == ()  # no lane started, so no money was spent
    assert len(queued) == 1
    question, detail = queued[0]
    assert question == supervise.PASS_SPEND_QUESTION
    assert "8000" in detail  # the combined forecast...
    assert "5000" in detail  # ...and the remainder it will not fit
    assert "epic.1" in detail and "epic.2" in detail


def test_no_module_level_name_in_supervise_is_bound_twice() -> None:
    """A second binding of a queue-key constant is invisible and silently wins.

    `PASS_SPEND_QUESTION` was bound at two module levels with byte-identical text.
    The later one won at import, so the copy a reader finds first — beside
    `PassSpendAdmission`, where the question belongs — was dead: editing it changed
    nothing, while `decisions.enqueue` keys items by (issue, kind, question), so the
    queue would have gone on filing under the old string (basicly-tcmy.3). That is
    the exact failure basicly-jr0l.52 named, and the assertion meant to catch it
    compared the enqueued question against the module global, which agrees with
    itself however many copies exist.

    Scanned from the source, not the imported module: by import time the duplication
    is already resolved and only the survivor is left to look at, which is what made
    this invisible.
    """
    source = Path(supervise.__file__).resolve().read_text(encoding="utf-8")
    bound: dict[str, int] = {}
    for node in ast.parse(source).body:
        targets = (
            node.targets
            if isinstance(node, ast.Assign)
            else [node.target]
            if isinstance(node, ast.AnnAssign)
            else []
        )
        for target in targets:
            if isinstance(target, ast.Name):
                bound[target.id] = bound.get(target.id, 0) + 1

    assert [name for name, count in bound.items() if count > 1] == []


def test_the_queued_pass_refusal_carries_whatever_the_binding_says(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The consumer reads the live binding, so editing it changes what is enqueued.

    The other half of the guard: one binding is only worth having if the enqueue path
    actually reads it. A sentinel proves the question is not a third hardcoded copy of
    the same sentence sitting at the call site.
    """
    asked: list[str] = []
    monkeypatch.setattr(
        supervise.decisions,
        "enqueue",
        lambda _r, _i, _k, question, _d=None: asked.append(question),
    )
    monkeypatch.setattr(supervise, "PASS_SPEND_QUESTION", "sentinel question")

    supervise.record_pass_refusal(
        Path(),
        "epic",
        supervise.PassSpendAdmission(8_000, 5_000, ("epic.1",), (), "8000 over 5000"),
    )

    assert asked == ["sentinel question"]


def test_pass_is_admitted_when_its_forecast_fits_the_remainder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The control: a gate that refused every pass would pass the test above.

    Same two lanes, same remainder, forecasts that fit — every lane must dispatch
    and nothing may be queued.
    """
    _patch_readiness(monkeypatch, ranked=((1, "epic.1"), (2, "epic.2")))
    queued = _pass_fixture(
        monkeypatch,
        sizings={"epic.1": _dispatch_sizing(20_000), "epic.2": _dispatch_sizing(30_000)},
        forecasts={"epic.1": 2_000, "epic.2": 2_000},
    )

    outcomes = supervise.dispatch_lanes(
        Path(),
        _session(_lane("epic.1"), _lane("epic.2")),
        admission=_granted("L3", 10_000, 5_000),
    )

    assert sorted(o.issue_id for o in outcomes) == ["epic.1", "epic.2"]
    assert queued == []


def test_a_lane_queued_behind_the_cap_does_not_start_once_the_budget_is_gone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The acceptance criterion: a fan-out that exhausts its grant starts no further lanes.

    Admission was a pass-entry verdict, so lanes waiting behind the concurrency cap
    were cleared to run on a reading taken before any of them had spent anything, and
    nothing re-checked once the runners were live. Measured: a pass admitted at a
    16316972 forecast ran to 43599830 against a 21000000 grant, and the halt printed
    only after the last lane exited (basicly-jr0l.59).

    ``cap=1`` is what makes the second lane *queued* rather than concurrent, which is
    the only shape this guard can bound - a lane already running is never interrupted.
    """
    _patch_readiness(monkeypatch, ranked=((1, "epic.1"), (2, "epic.2")))
    _pass_fixture(
        monkeypatch,
        sizings={"epic.1": _dispatch_sizing(20_000), "epic.2": _dispatch_sizing(20_000)},
        forecasts={"epic.1": 1_000, "epic.2": 1_000},
    )
    # The grant is intact when the first lane starts and gone by the second's turn.
    readings = iter([_granted("L3", 10_000, 0), _granted("L3", 10_000, 10_000)])
    monkeypatch.setattr(supervise.policy, "spend_status", lambda *_a, **_k: next(readings))

    outcomes = supervise.dispatch_lanes(
        Path(),
        _session(_lane("epic.1"), _lane("epic.2")),
        cap=1,
        admission=_granted("L3", 10_000, 0),
    )

    assert [outcome.issue_id for outcome in outcomes] == ["epic.1", "epic.2"]
    assert outcomes[0].detail == "test"  # dispatched, because the budget still covered it
    assert "not started" in outcomes[1].detail
    assert outcomes[1].result is None  # and it really did not run


def test_pass_forecast_ignores_a_lane_the_band_already_refuses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A lane that will not dispatch cannot be charged to the pass.

    ``epic.2`` is above the working-set ceiling, so the band holds it and it spends
    nothing. Counting its forecast anyway would refuse the pass over money nobody
    was going to spend, and the two gates would compound into a wedge.
    """
    _patch_readiness(monkeypatch, ranked=((1, "epic.1"), (2, "epic.2")))
    queued = _pass_fixture(
        monkeypatch,
        sizings={"epic.1": _dispatch_sizing(20_000), "epic.2": _dispatch_sizing(100_000)},
        forecasts={"epic.1": 4_000, "epic.2": 90_000},
    )

    outcomes = supervise.dispatch_lanes(
        Path(),
        _session(_lane("epic.1"), _lane("epic.2")),
        admission=_granted("L3", 10_000, 5_000),
    )

    # The in-band lane still runs: 4000 fits the 5000 remainder on its own.
    assert [o.issue_id for o in outcomes] == ["epic.1", "epic.2"]
    assert queued == []


def test_pass_names_the_lanes_it_could_not_forecast(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An assumed bound must never be presented as a measured forecast.

    Most open beads carry no ``## Scope``, so an unsizeable lane is the common case.
    It is counted at the conservative bound rather than skipped (basicly-vz78), and
    the message keeps the two apart — the same seeded-vs-measured honesty
    basicly-jr0l.21 built into the forecast.
    """
    _patch_readiness(monkeypatch, ranked=((1, "epic.1"), (2, "epic.2")))
    queued = _pass_fixture(
        monkeypatch,
        sizings={"epic.1": _dispatch_sizing(20_000), "epic.2": None},
        forecasts={"epic.1": 9_000},
        dispatch_is_a_failure=True,
    )

    assert (
        supervise.dispatch_lanes(
            Path(),
            _session(_lane("epic.1"), _lane("epic.2")),
            admission=_granted("L3", 10_000, 5_000),
        )
        == ()
    )

    _question, detail = queued[0]
    # 9000 measured for epic.1 plus the 1000 bound assumed for epic.2.
    assert "10000 tokens forecast" in detail
    assert "sized: epic.1" in detail
    assert "assumed at the unsizeable-lane bound (measured): epic.2" in detail


def test_an_unsizeable_lane_is_bounded_rather_than_waved_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pass that made this gate inert: no forecast used to mean no ceiling.

    ``dispatch_sizing`` returns None for any bead with no ``## Scope``, and the gate
    read that as "nothing to compare" and admitted — so a pass of hand-filed lanes had
    no forward bound at all, and one lane spent 4079243 tokens against a 3000000
    ceiling (basicly-vz78). An assumed bound can be wrong; no bound cannot be right.
    """
    _patch_readiness(monkeypatch, ranked=((1, "epic.1"),))
    queued = _pass_fixture(
        monkeypatch,
        sizings={"epic.1": None},
        forecasts={},
        dispatch_is_a_failure=True,
        unsized_tokens=5_000,
    )

    assert (
        supervise.dispatch_lanes(
            Path(), _session(_lane("epic.1")), admission=_granted("L3", 10_000, 9_999)
        )
        == ()
    )

    _question, detail = queued[0]
    assert "assumed at the unsizeable-lane bound (measured): epic.1" in detail


def test_an_unsizeable_lane_still_dispatches_when_the_budget_covers_its_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bounding is not banning — the distinction the fail-open behaviour was defending.

    Failing closed on an absent forecast would turn a spend governor into a ban on
    hand-filed work, which is why it used to admit unconditionally. Counting the lane
    at a conservative figure keeps hand-filed work dispatchable while still refusing
    the case that actually overruns: a remainder too small to fund one lane.
    """
    _patch_readiness(monkeypatch, ranked=((1, "epic.1"),))
    queued = _pass_fixture(
        monkeypatch,
        sizings={"epic.1": None},
        forecasts={},
        unsized_tokens=1_000,
    )

    outcomes = supervise.dispatch_lanes(
        Path(), _session(_lane("epic.1")), admission=_granted("L3", 10_000, 0)
    )

    assert [o.issue_id for o in outcomes] == ["epic.1"]
    assert queued == []


def test_the_pass_reports_its_spend_coverage_even_when_it_admits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The silence was half the defect: an unbounded pass looked like a checked one.

    ``violation=None`` reads identically whether the gate compared real forecasts or
    had nothing to compare, and no caller printed the difference (basicly-vz78). The
    coverage line is therefore emitted on the admitted path too.
    """
    _patch_readiness(monkeypatch, ranked=((1, "epic.1"),))
    _pass_fixture(monkeypatch, sizings={"epic.1": None}, forecasts={}, unsized_tokens=1_000)
    lines: list[str] = []

    supervise.dispatch_lanes(
        Path(),
        _session(_lane("epic.1")),
        admission=_granted("L3", 10_000, 0),
        report=lines.append,
    )

    assert any("assumed at the unsizeable-lane bound" in line for line in lines)
    assert any("epic.1" in line for line in lines)


def test_the_pass_reports_which_lanes_the_band_never_checked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same silence, one gate over: nothing said which lanes were measured.

    The band printed nothing at all, so a pass where every lane was unsizeable read
    exactly like one where every estimate fitted — and on the run that measured it all
    four lanes were the former (basicly-jr0l.60).
    """
    _patch_readiness(monkeypatch, ranked=((1, "epic.1"), (2, "epic.2")))
    _pass_fixture(
        monkeypatch,
        sizings={"epic.1": _dispatch_sizing(20_000), "epic.2": None},
        forecasts={"epic.1": 9_000},
        unsized_tokens=1_000,
    )
    lines: list[str] = []

    supervise.dispatch_lanes(
        Path(),
        _session(_lane("epic.1"), _lane("epic.2")),
        admission=_granted("L3", 100_000, 0),
        report=lines.append,
    )

    band = next(line for line in lines if line.startswith("band:"))
    assert "checked: epic.1" in band
    assert f"NEVER CHECKED ({decompose.SCOPE_UNDECLARED}): epic.2" in band


def test_pass_spend_is_not_enforced_without_a_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An ungranted session has no ceiling, so an enormous forecast still dispatches."""
    _patch_readiness(monkeypatch, ranked=((1, "epic.1"),))
    queued = _pass_fixture(
        monkeypatch,
        sizings={"epic.1": _dispatch_sizing(20_000)},
        forecasts={"epic.1": 10_000_000},
    )

    outcomes = supervise.dispatch_lanes(Path(), _session(_lane("epic.1")), admission=_UNGRANTED)

    assert [o.issue_id for o in outcomes] == ["epic.1"]
    assert queued == []


def test_pass_sizes_each_lane_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The band admission is computed at the pass and handed down, not re-derived.

    Estimating twice would double the tracker reads a pass makes and let the gate
    that sums the forecast disagree with the gate that admits the lane.
    """
    _patch_readiness(monkeypatch, ranked=((1, "epic.1"), (2, "epic.2")))
    _pass_fixture(
        monkeypatch,
        sizings={"epic.1": _dispatch_sizing(20_000), "epic.2": _dispatch_sizing(30_000)},
        forecasts={"epic.1": 1_000, "epic.2": 1_000},
    )
    sized: list[str] = []
    real = supervise.admit_working_set

    def counting(repo_root, issue_id, sizing):
        sized.append(issue_id)
        return real(repo_root, issue_id, sizing)

    def dispatch(_repo, _session, lane, _spec, _sizing, **kw):
        # A lane handed no admission would have to estimate again, so record that
        # here rather than trusting the parameter to be threaded.
        if kw.get("working_set") is None:
            sized.append(f"{lane.issue_id}:re-sized")
        return _outcome(lane.issue_id)

    monkeypatch.setattr(supervise, "admit_working_set", counting)
    monkeypatch.setattr(supervise, "_dispatch_lane", dispatch)

    supervise.dispatch_lanes(
        Path(),
        _session(_lane("epic.1"), _lane("epic.2")),
        admission=_granted("L3", 10_000, 0),
        cap=1,
    )

    assert sorted(sized) == ["epic.1", "epic.2"]


# --- Stale worktree bindings (basicly-1koh) -----------------------------------


def test_a_stale_binding_with_nothing_unlanded_is_cleared(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The bead stops deriving `build`, so the next fan-out can re-provision it."""
    monkeypatch.setattr(loop, "_worktree_landed", lambda *_a: True)
    cleared: list[str] = []
    monkeypatch.setattr(loop, "clear_worktree_binding", lambda _r, iid: cleared.append(iid))
    monkeypatch.setattr(
        decisions, "enqueue", lambda *_a, **_k: pytest.fail("nothing to escalate here")
    )

    routed = supervise.repair_stale_bindings(tmp_path, _session(_lane("epic.1", live=False)))

    assert cleared == ["epic.1"]
    assert [(r.issue_id, r.route) for r in routed] == [("epic.1", "repaired")]


def test_a_stale_binding_over_unlanded_commits_is_escalated_not_cleared(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Clearing it would make those commits unreachable from the loop, so it refuses."""
    monkeypatch.setattr(loop, "_worktree_landed", lambda *_a: False)
    monkeypatch.setattr(
        loop,
        "clear_worktree_binding",
        lambda *_a: pytest.fail("an unlanded branch must not be unbound"),
    )
    asked: list[tuple[str, str]] = []
    monkeypatch.setattr(
        decisions,
        "enqueue",
        lambda _r, iid, kind, *_a, **_k: asked.append((iid, kind)),
    )

    routed = supervise.repair_stale_bindings(tmp_path, _session(_lane("epic.1", live=False)))

    assert asked == [("epic.1", "escalation")]
    assert [(r.issue_id, r.route) for r in routed] == [("epic.1", "decision")]
    assert "unlanded commits" in routed[0].detail


def test_a_live_lane_is_never_repaired(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The repair keys on liveness alone; a working lane must be left to dispatch."""
    monkeypatch.setattr(
        loop, "_worktree_landed", lambda *_a: pytest.fail("a live lane is not inspected")
    )

    assert supervise.repair_stale_bindings(tmp_path, _session(_lane("epic.1", live=True))) == ()


def test_a_repair_counts_as_progress_so_the_pass_re_derives() -> None:
    """Nothing landed, but what the next derivation sees changed (basicly-1koh).

    Without this the supervisor reported "no ready lanes and nothing to land" and
    exited on the very pass that had just unblocked a lane.
    """
    assert supervise.should_continue((supervise.RoutedOutcome("epic.1", "repaired", "cleared"),))


# --- Cold-root seeding (basicly-t73d) ------------------------------------------


def _seed_fixture(
    monkeypatch: pytest.MonkeyPatch,
    *,
    steps: tuple[loop.AdvanceResult, ...],
    derived: supervise.SessionState | None = None,
) -> list[str]:
    """A pass with no dispatchable lanes whose root advance yields *steps*.

    *derived* is the session the pass re-reads *after* seeding — the state in which the
    new worktrees exist. Stubbed rather than left to reach ``br``, because seeding
    re-derives on purpose (basicly-jr0l.57) and a real read would spawn a subprocess in
    a unit test. Defaults to the unchanged cold session, so a fixture that says nothing
    about the post-seed state describes a pass that provisioned nothing.
    """
    _patch_readiness(monkeypatch)
    advanced: list[str] = []
    after = derived if derived is not None else _cold_session()

    def fake_run_until_blocked(_repo: Path, issue_id: str) -> tuple[loop.AdvanceResult, ...]:
        advanced.append(issue_id)
        return steps

    monkeypatch.setattr(supervise.loop, "run_until_blocked", fake_run_until_blocked)
    monkeypatch.setattr(supervise, "derive_session", lambda _r, _i: after)
    return advanced


def _step(action: str, *, to_phase: str = "build") -> loop.AdvanceResult:
    return loop.AdvanceResult("epic", "decompose", to_phase, action, f"{action} detail")


def _cold_session() -> supervise.SessionState:
    """A root with one open child and nothing adopted — the cold-start shape."""
    return supervise.SessionState(
        root_issue="epic", root_status="open", children=(("epic.1", "open"),), adopted=()
    )


def test_a_cold_root_provisions_its_lanes_instead_of_reporting_nothing_to_land(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The defect three handovers documented as the command that runs the factory.

    `loop supervise <root>` alone dispatched nothing, because nothing on its path
    provisions a worktree and only a bound worktree derives `build` (basicly-t73d).
    """
    advanced = _seed_fixture(monkeypatch, steps=(_step("decomposed"),))

    routed = supervise.seed_lanes(tmp_path, _cold_session())

    assert advanced == ["epic"], "the root itself must be advanced to fan out its children"
    assert [(r.issue_id, r.route) for r in routed] == [("epic", "seeded")]
    assert supervise.should_continue(routed), "the seeded lanes must be dispatched next pass"


def test_a_root_that_cannot_seed_stops_the_session_rather_than_spinning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Termination guard: seeding is only retriable when it actually progressed.

    A blocked root advance repeated forever would be a busy loop holding the singleton
    lock, which is strictly worse than the exit it replaced.
    """
    _seed_fixture(monkeypatch, steps=(_step("blocked", to_phase="decompose"),))

    routed = supervise.seed_lanes(tmp_path, _cold_session())

    assert [r.route for r in routed] == ["seed-blocked"]
    assert not supervise.should_continue(routed)
    assert "1 open child(ren)" in routed[0].detail


def test_a_blocked_root_that_provisioned_lanes_routes_seeded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The documented one-command run, which reported failure after succeeding.

    A package root parked awaiting its children can never advance, so routing on the
    root's own progress declared `seed-blocked` on a pass that had just built five
    worktrees — and `seed-blocked` is deliberately non-retriable, so the session ended
    and threw the lanes away. Running the identical command again dispatched them
    (basicly-jr0l.57).
    """
    advanced = _seed_fixture(
        monkeypatch,
        steps=(_step("blocked", to_phase="decompose"),),
        derived=_session(_lane("epic.1"), _lane("epic.2")),
    )

    routed = supervise.seed_lanes(tmp_path, _cold_session())

    assert advanced == ["epic"]
    assert [(r.issue_id, r.route) for r in routed] == [("epic", "seeded")]
    assert supervise.should_continue(routed), "the pass must go on to dispatch what it built"
    assert "provisioned 2 dispatchable lane(s)" in routed[0].detail


def test_provisioned_lanes_that_cannot_dispatch_say_so_instead_of_claiming_none_exist(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Termination still wins, but the message may not contradict the worktrees on disk.

    Nothing another pass could change, so the route stays non-retriable — the complaint
    behind basicly-jr0l.57 was that the detail was false in its own terms.
    """
    _seed_fixture(
        monkeypatch,
        steps=(_step("blocked", to_phase="decompose"),),
        derived=_session(_lane("epic.1")),
    )
    monkeypatch.setattr(supervise.decisions, "has_pending", lambda _r, _i: True)

    routed = supervise.seed_lanes(tmp_path, _cold_session())

    assert [r.route for r in routed] == ["seed-blocked"]
    assert not supervise.should_continue(routed)
    assert "provisioned 1 lane(s) but none is dispatchable" in routed[0].detail
    assert "epic.1" in routed[0].detail


def test_seeding_is_skipped_while_a_lane_is_already_dispatchable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Re-advancing the root with lanes in flight would provision past the cap."""
    _patch_readiness(monkeypatch, ranked=((1, "epic.1"),))
    monkeypatch.setattr(
        supervise.loop,
        "run_until_blocked",
        lambda *_a: pytest.fail("a pass with work to dispatch must not re-seed"),
    )

    assert supervise.seed_lanes(tmp_path, _session(_lane("epic.1"))) == ()


def test_seeding_is_skipped_when_the_root_has_no_open_children(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Nothing to provision, so the session is simply done."""
    _patch_readiness(monkeypatch)
    monkeypatch.setattr(
        supervise.loop,
        "run_until_blocked",
        lambda *_a: pytest.fail("there is no child to fan out"),
    )

    empty = supervise.SessionState("epic", "open", children=(), adopted=())
    assert supervise.seed_lanes(tmp_path, empty) == ()


# --- A metered dispatch needs a budget (basicly-kkux) ---------------------------


_HEADLESS_SPEC = runner.RunnerSpec("claude", runner.HEADLESS, command=("claude", "-p", "{prompt}"))


def _ungranted() -> policy.SpendStatus:
    """What `policy.spend_status` returns for a root with no grant at all."""
    return policy.SpendStatus(grant=None, spent_tokens=0, halted=False)


def test_a_metered_dispatch_is_refused_without_a_covering_budget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """With no grant there is no ceiling at all, not merely a loose one (basicly-kkux).

    `spend_status` reports halted=False and `check_pass_spend` admits any forecast
    against a None remainder, so an ungranted session dispatched headless agents with
    no bound — latent until basicly-t73d let the supervisor seed its own lanes.
    """
    _patch_readiness(monkeypatch, ranked=((1, "epic.1"),))
    monkeypatch.setattr(supervise.runner, "select_runner", lambda *_a, **_k: _HEADLESS_SPEC)
    monkeypatch.setattr(
        supervise,
        "_dispatch_lane",
        lambda *_a, **_k: pytest.fail("an unbudgeted metered dispatch must not start"),
    )
    queued: list[tuple[str, str]] = []
    monkeypatch.setattr(
        supervise.decisions,
        "enqueue",
        lambda _r, _i, _k, q, d="", **_kw: queued.append((q, d)),
    )
    lines: list[str] = []

    outcomes = supervise.dispatch_lanes(
        tmp_path, _session(_lane("epic.1")), admission=_ungranted(), report=lines.append
    )

    assert outcomes == ()
    assert queued, "the refusal must reach the decision queue, not just stdout"
    assert "no grant with a token budget" in queued[0][1]
    assert any("refused" in line for line in lines)


def test_a_handoff_dispatch_needs_no_budget_because_it_spends_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The exemption that keeps interactive driving working with no grant issued."""
    _patch_readiness(monkeypatch, ranked=((1, "epic.1"),))
    monkeypatch.setattr(supervise.runner, "select_runner", lambda *_a, **_k: _MANUAL_SPEC)
    monkeypatch.setattr(supervise.decompose, "unsized_lane_tokens", lambda *_a: (10, "measured"))
    monkeypatch.setattr(
        supervise, "_dispatch_lane", lambda _r, _s, lane, *_a, **_kw: _outcome(lane.issue_id)
    )

    outcomes = supervise.dispatch_lanes(tmp_path, _session(_lane("epic.1")), admission=_ungranted())

    assert [o.issue_id for o in outcomes] == ["epic.1"]


def test_a_metered_dispatch_proceeds_once_a_budget_covers_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The refusal must key on the missing budget, not on the runner being headless."""
    _patch_readiness(monkeypatch, ranked=((1, "epic.1"),))
    monkeypatch.setattr(supervise.runner, "select_runner", lambda *_a, **_k: _HEADLESS_SPEC)
    monkeypatch.setattr(supervise.decompose, "unsized_lane_tokens", lambda *_a: (10, "measured"))
    monkeypatch.setattr(
        supervise, "_dispatch_lane", lambda _r, _s, lane, *_a, **_kw: _outcome(lane.issue_id)
    )

    outcomes = supervise.dispatch_lanes(
        tmp_path, _session(_lane("epic.1")), admission=_granted("L2", 5_000, 0)
    )

    assert [o.issue_id for o in outcomes] == ["epic.1"]


def test_seeding_does_not_provision_for_a_dispatch_that_cannot_start(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Provisioning is expensive, so the budget is checked before it, not after.

    Measured: an ungranted pass provisioned five worktrees — a `uv sync` and an
    `npm install` each — and only then refused the dispatch (basicly-kkux).
    """
    _patch_readiness(monkeypatch)
    monkeypatch.setattr(supervise, "metered_without_a_budget", lambda *_a: "claude")
    monkeypatch.setattr(
        supervise.loop,
        "run_until_blocked",
        lambda *_a: pytest.fail("nothing may be provisioned for a dispatch that cannot start"),
    )

    routed = supervise.seed_lanes(tmp_path, _cold_session(), admission=_ungranted())

    assert [r.route for r in routed] == ["seed-blocked"]
    assert not supervise.should_continue(routed)
    assert "no grant with a token budget" in routed[0].detail


# --- Dispatch observability (basicly-e5a6, basicly-vu6u) ------------------------


def test_the_dispatch_note_names_the_tier_and_the_resolved_model() -> None:
    """Naming the adapter says nothing about which model actually ran."""
    outcome = supervise.LaneOutcome(
        issue_id="epic.1",
        runner_name="claude",
        result=None,
        needs_fact=None,
        occupancy=None,
        overrun=False,
        followup_id=None,
        detail="finished",
        model="claude-opus-5",
        model_tier="high",
        model_source="agent-tier",
        observed_models=("claude-opus-5",),
        tier_honoured=True,
    )

    note = outcome.model_note

    assert "tier high" in note
    assert "claude-opus-5" in note
    assert "agent-tier" in note
    assert "NOT HONOURED" not in note


def test_the_dispatch_note_flags_a_tier_that_was_not_honoured() -> None:
    """A silently demoted dispatch used to read exactly like a correct one."""
    outcome = supervise.LaneOutcome(
        issue_id="epic.1",
        runner_name="claude",
        result=None,
        needs_fact=None,
        occupancy=None,
        overrun=False,
        followup_id=None,
        detail="finished",
        model="claude-opus-5",
        model_tier="maximum",
        model_source="agent-tier",
        observed_models=("claude-haiku-4-5",),
        tier_honoured=False,
    )

    note = outcome.model_note

    assert "TIER NOT HONOURED" in note
    assert "observed claude-haiku-4-5" in note, "the disagreement itself must be visible"


def test_a_dispatch_with_no_model_information_adds_no_note() -> None:
    """A handoff has no model to name, and an empty bracket would be noise."""
    outcome = supervise.LaneOutcome(
        issue_id="epic.1",
        runner_name="manual",
        result=None,
        needs_fact=None,
        occupancy=None,
        overrun=False,
        followup_id=None,
        detail="handed off",
    )

    assert outcome.model_note == ""


def test_a_running_lane_reports_its_elapsed_time_while_it_runs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Measured: the log stood still for 519.6s on a healthy lane (basicly-vu6u).

    The heartbeat that keeps the singleton lock fresh already ticks during dispatch, so
    the progress line costs no extra machinery.
    """
    _patch_readiness(monkeypatch, ranked=((1, "epic.1"),))
    monkeypatch.setattr(supervise.runner, "select_runner", lambda *_a, **_k: _MANUAL_SPEC)
    monkeypatch.setattr(supervise.decompose, "unsized_lane_tokens", lambda *_a: (10, "measured"))
    monkeypatch.setattr(supervise, "HEARTBEAT_INTERVAL_S", 0.01)

    def slow_dispatch(_r, _s, lane, *_a, **_kw):
        time.sleep(0.2)
        return _outcome(lane.issue_id)

    monkeypatch.setattr(supervise, "_dispatch_lane", slow_dispatch)
    lines: list[str] = []

    supervise.dispatch_lanes(
        tmp_path,
        _session(_lane("epic.1")),
        admission=_granted("L2", 5_000, 0),
        report=lines.append,
    )

    running = [line for line in lines if line.startswith("running:")]
    assert running, "a lane in flight must report before it finishes, not only after"
    assert "epic.1" in running[0]
