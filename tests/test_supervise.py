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
import subprocess
import threading
import time
from pathlib import Path

import pytest

from basicly import (
    decisions,
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
    monkeypatch.setattr(supervise, "_has_subtasks", lambda _r, _i: False)
    # Ungranted sessions have no ceiling to enforce, which is the state these
    # tests are about; the halt itself is pinned separately, below.
    monkeypatch.setattr(supervise.policy, "spend_status", lambda *_a, **_k: _UNGRANTED)


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

    def fake_dispatch(_repo, _session, lane, _spec, _sizing) -> supervise.LaneOutcome:
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

    def fake_dispatch(_repo, _session, lane, _spec, _sizing) -> supervise.LaneOutcome:
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

    def fake_dispatch(_repo, _session, lane, _spec, _sizing) -> supervise.LaneOutcome:
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

    def fake_dispatch(_repo, _session, lane, _spec, _sizing) -> supervise.LaneOutcome:
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
    """Under budget is not halted — the ceiling must not stop a funded session."""
    _patch_readiness(monkeypatch, ranked=((1, "epic.1"),))
    monkeypatch.setattr(supervise.runner, "select_runner", lambda *_a, **_k: _MANUAL_SPEC)
    monkeypatch.setattr(
        supervise, "_dispatch_lane", lambda _r, _s, lane, *_a: _outcome(lane.issue_id)
    )
    monkeypatch.setattr(
        supervise.decisions,
        "enqueue",
        lambda *_a, **_k: pytest.fail("nothing to escalate while inside the budget"),
    )

    outcomes = supervise.dispatch_lanes(
        Path(), _session(_lane("epic.1")), admission=_granted("L2", 5000, 4999)
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
