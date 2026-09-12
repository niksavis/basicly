from __future__ import annotations

import ast
import dataclasses
import json
import os
import subprocess
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future
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
    tracker,
    working_set,
)
from basicly.config import PolicyConfig, RunnerConfig, SizingConfig
from basicly.supervise import LOCK_FILE, STALE_AFTER_S, LockHeldError, LockLostError
from tests import fake_tracker


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


def test_acquire_creates_lock_with_session_payload(tmp_path: Path) -> None:
    lock = supervise.acquire(tmp_path, "epic:abcd1234", "epic")
    payload = json.loads(lock.read_text(encoding="utf-8"))
    assert payload["pid"] == os.getpid()
    assert payload["session_id"] == "epic:abcd1234"
    assert payload["root_issue"] == "epic"
    assert (tmp_path / ".basicly/usage/.gitignore").read_text(encoding="utf-8") == "*\n"


def test_second_acquire_refuses_while_first_heartbeats(tmp_path: Path) -> None:
    supervise.acquire(tmp_path, "epic:first", "epic")
    with pytest.raises(LockHeldError, match="epic:first"):
        supervise.acquire(tmp_path, "epic:second", "epic")


def test_heartbeat_keeps_an_aging_lock_fresh(tmp_path: Path) -> None:
    lock = supervise.acquire(tmp_path, "epic:first", "epic")
    _backdate(lock, STALE_AFTER_S + 5)
    supervise.heartbeat(lock, "epic:first")
    with pytest.raises(LockHeldError, match="epic:first"):
        supervise.acquire(tmp_path, "epic:second", "epic")


def test_heartbeat_raises_lock_lost_when_lock_vanished(tmp_path: Path) -> None:
    lock = supervise.acquire(tmp_path, "epic:first", "epic")
    lock.unlink()
    with pytest.raises(LockLostError):
        supervise.heartbeat(lock, "epic:first")


def test_stalled_holder_heartbeat_fences_after_takeover(tmp_path: Path) -> None:

    lock = supervise.acquire(tmp_path, "epic:first", "epic")
    _backdate(lock, STALE_AFTER_S + 1)
    supervise.acquire(tmp_path, "epic:successor", "epic")
    with pytest.raises(LockLostError, match="successor"):
        supervise.heartbeat(lock, "epic:first")


def test_stale_lock_is_taken_over_atomically(tmp_path: Path) -> None:
    lock = supervise.acquire(tmp_path, "epic:crashed", "epic")
    _backdate(lock, STALE_AFTER_S + 1)
    took = supervise.acquire(tmp_path, "epic:successor", "epic")
    payload = json.loads(took.read_text(encoding="utf-8"))
    assert payload["session_id"] == "epic:successor"
    assert not list(lock.parent.glob("*.stale.*"))


def test_takeover_loser_gets_lock_held(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lock = supervise.acquire(tmp_path, "epic:crashed", "epic")
    _backdate(lock, STALE_AFTER_S + 1)

    def losing_replace(_self: object, _dst: object) -> None:
        raise FileNotFoundError

    monkeypatch.setattr(supervise.Path, "replace", losing_replace)
    with pytest.raises(LockHeldError, match="taking over"):
        supervise.acquire(tmp_path, "epic:loser", "epic")


def test_acquire_retries_when_lock_freed_mid_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock = supervise.acquire(tmp_path, "epic:first", "epic")

    real_read_holder = supervise.read_holder

    def freeing_read_holder(repo_root: Path) -> object:
        lock.unlink(missing_ok=True)
        return real_read_holder(repo_root)

    monkeypatch.setattr(supervise, "read_holder", freeing_read_holder)
    took = supervise.acquire(tmp_path, "epic:second", "epic")
    payload = json.loads(took.read_text(encoding="utf-8"))
    assert payload["session_id"] == "epic:second"


def test_takeover_replaces_an_abandoned_same_pid_tombstone(tmp_path: Path) -> None:
    lock = supervise.acquire(tmp_path, "epic:crashed", "epic")
    _backdate(lock, STALE_AFTER_S + 1)
    leaked = lock.with_name(f"{lock.name}.stale.{os.getpid()}")
    leaked.write_text("{}", encoding="utf-8")
    took = supervise.acquire(tmp_path, "epic:successor", "epic")
    assert json.loads(took.read_text(encoding="utf-8"))["session_id"] == "epic:successor"


def test_corrupt_fresh_lock_still_refuses(tmp_path: Path) -> None:
    path = _lock_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("not json", encoding="utf-8")
    with pytest.raises(LockHeldError, match="unknown"):
        supervise.acquire(tmp_path, "epic:second", "epic")


def test_release_removes_only_own_lock(tmp_path: Path) -> None:
    lock = supervise.acquire(tmp_path, "epic:first", "epic")
    supervise.release(lock, "epic:someone-else")
    assert lock.exists()
    supervise.release(lock, "epic:first")
    assert not lock.exists()
    supervise.release(lock, "epic:first")


class _FakeBrShow:
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
    issues = {
        "epic": _issue("epic", children=(("epic.1", "in_progress"), ("epic.2", "open"))),
        "epic.1": _issue("epic.1", "in_progress", external_ref="worktree:epic-1:harness/epic-1"),
        "epic.2": _issue("epic.2", "open"),
    }
    _install_br(monkeypatch, _FakeBrShow(issues))
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
    issues = {
        "epic": _issue("epic", children=(("epic.1", "in_progress"), ("epic.2", "closed"))),
        "epic.1": _issue("epic.1", "in_progress", external_ref="worktree:epic-1:harness/epic-1"),
    }
    _install_br(monkeypatch, _FakeBrShow(issues))
    _fake_sessions(monkeypatch, set())

    state = supervise.derive_session(tmp_path, "epic")

    assert [lane.issue_id for lane in state.adopted] == ["epic.1"]
    assert state.adopted[0].live is False
    assert state.open_children == ("epic.1",)


def test_derive_session_adopts_leaf_root_binding(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    issues = {
        "task": _issue("task", "in_progress", external_ref="worktree:task:harness/task"),
    }
    _install_br(monkeypatch, _FakeBrShow(issues))
    _fake_sessions(monkeypatch, {"task"})

    state = supervise.derive_session(tmp_path, "task")

    assert [lane.issue_id for lane in state.adopted] == ["task"]
    assert state.children == ()
    assert state.done is False


def test_derive_session_done_when_all_children_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    issues = {
        "epic": _issue("epic", children=(("epic.1", "closed"), ("epic.2", "closed"))),
        "closed-root": _issue("closed-root", "closed"),
    }
    _install_br(monkeypatch, _FakeBrShow(issues))
    _fake_sessions(monkeypatch, set())

    assert supervise.derive_session(tmp_path, "epic").done is True
    assert supervise.derive_session(tmp_path, "closed-root").done is True


def test_a_deferred_child_is_neither_sized_nor_holds_the_session_open(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    issues = {
        "epic": _issue("epic", children=(("epic.1", "closed"), ("epic.2", "deferred"))),
        "epic.2": _issue("epic.2", "deferred"),
        "control": _issue("control", children=(("control.1", "closed"), ("control.2", "open"))),
        "control.2": _issue("control.2", "open"),
    }
    _install_br(monkeypatch, _FakeBrShow(issues))
    _fake_sessions(monkeypatch, set())

    deferred_only = supervise.derive_session(tmp_path, "epic")
    control = supervise.derive_session(tmp_path, "control")

    assert deferred_only.open_children == ()
    assert deferred_only.done is True
    assert control.open_children == ("control.2",)
    assert control.done is False


class _FakeBrSelection(_FakeBrShow):
    def __init__(self, issues: dict[str, dict], labelled: dict[str, tuple[str, ...]]) -> None:
        super().__init__(issues)
        self.labelled = labelled

    def __call__(self, repo_root: Path, args: list[str], *, _check: bool = True) -> _Proc:
        if args[:1] == ["list"]:
            label = args[args.index("--label") + 1]
            closed_only = "--status" in args
            records = [
                {"id": issue_id, "status": self.issues[issue_id]["status"]}
                for issue_id in self.labelled.get(label, ())
                if (self.issues[issue_id]["status"] == "closed") is closed_only
            ]
            return _Proc(json.dumps({"issues": records, "total": len(records)}))
        if args[:1] in (["update"], ["create"], ["dep"]):
            raise AssertionError(f"a lane selection must not write to the tracker: {args}")
        return super().__call__(repo_root, args, _check=_check)


def _cut_fixture(monkeypatch: pytest.MonkeyPatch) -> None:

    issues = {
        "release": _issue("release"),
        "origin.1": _issue("origin.1", "open") | {"parent": "origin"},
        "origin.2": _issue("origin.2", "in_progress") | {"parent": "origin"},
        "other.9": _issue("other.9", "open") | {"parent": "other"},
        "unrelated": _issue("unrelated", "open"),
    }
    _install_br(
        monkeypatch,
        _FakeBrSelection(issues, {"release-v0.7.0": ("origin.1", "origin.2", "other.9")}),
    )
    _fake_sessions(monkeypatch, set())


def test_a_labelled_cut_fans_out_over_beads_that_already_have_a_parent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    _cut_fixture(monkeypatch)

    state = supervise.derive_session(tmp_path, "release", lane_label="release-v0.7.0")

    assert state.children == (
        ("origin.1", "open"),
        ("origin.2", "in_progress"),
        ("other.9", "open"),
    )
    assert state.open_children == ("origin.1", "origin.2", "other.9")
    assert state.lane_label == "release-v0.7.0"
    assert supervise.derive_session(tmp_path, "release").children == ()


def test_an_unlabelled_bead_is_not_a_lane_of_the_cut(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    _cut_fixture(monkeypatch)

    state = supervise.derive_session(tmp_path, "release", lane_label="release-v0.7.0")

    assert "unrelated" not in dict(state.children)


def test_a_cut_whose_every_bead_closed_is_done_rather_than_blocked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    issues = {
        "release": _issue("release"),
        "done.1": _issue("done.1", "closed") | {"parent": "origin"},
        "done.2": _issue("done.2", "closed") | {"parent": "other"},
    }
    _install_br(monkeypatch, _FakeBrSelection(issues, {"cut": ("done.1", "done.2")}))
    _fake_sessions(monkeypatch, set())

    state = supervise.derive_session(tmp_path, "release", lane_label="cut")

    assert state.children == (("done.1", "closed"), ("done.2", "closed"))
    assert state.open_children == ()
    assert state.done is True


def test_a_selector_no_bead_carries_is_refused_not_derived_as_an_idle_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    issues = {"release": _issue("release", "open")}
    _install_br(monkeypatch, _FakeBrSelection(issues, {"root-only": ("release",)}))
    _fake_sessions(monkeypatch, set())

    with pytest.raises(supervise.LaneSelectionError, match="typo-v9"):
        supervise.derive_session(tmp_path, "release", lane_label="typo-v9")
    with pytest.raises(supervise.LaneSelectionError, match="root-only"):
        supervise.derive_session(tmp_path, "release", lane_label="root-only")


def test_new_session_id_binds_root_and_varies() -> None:
    first = supervise.new_session_id("epic")
    second = supervise.new_session_id("epic")
    assert first.startswith("epic:")
    assert first != second


class _FakeBr:
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


def _install_br(monkeypatch: pytest.MonkeyPatch, fake: Callable[..., object]) -> None:

    fake_tracker.install(monkeypatch, fake)
    fake_tracker.install_graph(monkeypatch, fake)


def test_parse_found_info_round_trips_the_marker(tmp_path: Path) -> None:
    fake = _FakeBr({})
    info = supervise.FoundInfo(
        kind="coupling",
        summary="config loader also reads runner windows",
        detail="split touched both",
        affects=("src/basicly/config.py", "epic.2"),
    )
    with pytest.MonkeyPatch.context() as mp:
        _install_br(mp, fake)
        supervise.record_found_info(tmp_path, "epic.1", info)
        records = supervise.found_info_records(tmp_path, ["epic.1"])
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
    with pytest.raises(ValueError, match="unknown found-info kind"):
        supervise.record_found_info(
            Path(), "epic.1", supervise.FoundInfo(kind="rumor", summary="s")
        )


def test_parse_found_info_skips_malformed_records() -> None:
    assert supervise.parse_found_info("a plain comment", "x") is None
    assert supervise.parse_found_info("[harness-info] not json", "x") is None
    assert supervise.parse_found_info('[harness-info] {"kind":"rumor","summary":"s"}', "x") is None
    assert supervise.parse_found_info('[harness-info] {"kind":"fact","summary":" "}', "x") is None
    assert supervise.parse_found_info('[harness-info] ["not","object"]', "x") is None


def _coupling_session(*, in_flight: tuple[str, ...] = ()) -> supervise.SessionState:
    return supervise.SessionState(
        root_issue="epic",
        root_status="open",
        children=(("epic.1", "in_progress"), ("epic.2", "open")),
        adopted=tuple(_lane(issue_id) for issue_id in in_flight),
    )


def _coupling_issues(scope: str = "", deps: tuple[dict, ...] = ()) -> dict[str, dict]:
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
    repo_root: Path,
) -> tuple[tuple[str, str, str], ...]:
    _install_br(monkeypatch, fake)
    return supervise.propose_coupling_edges(repo_root, session)


def test_a_coupling_record_gates_a_bead_that_has_not_started(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _FakeBr(
        _coupling_issues(),
        comments={"epic.1": [_fold_marker("coupling", "both read the loader", ["epic.2"])]},
    )

    recorded = _propose(monkeypatch, fake, _coupling_session(in_flight=("epic.1",)), tmp_path)

    assert recorded == (("epic.2", "epic.1", "blocks"),)
    assert ("epic.2", "epic.1", "-t", "blocks") in fake.deps


def test_a_coupling_record_teaches_an_in_flight_lane_without_gating_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    fake = _FakeBr(
        _coupling_issues(),
        comments={"epic.1": [_fold_marker("coupling", "both read the loader", ["epic.2"])]},
    )

    recorded = _propose(
        monkeypatch, fake, _coupling_session(in_flight=("epic.1", "epic.2")), tmp_path
    )

    assert recorded == (("epic.2", "epic.1", supervise.merge.COUPLING_DEP_TYPE),)
    assert not any(dep[-1] == "blocks" for dep in fake.deps)


def test_a_coupling_record_reaches_a_bead_by_scope_overlap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _FakeBr(
        _coupling_issues(scope="src/basicly/loop.py"),
        comments={
            "epic.1": [_fold_marker("coupling", "the loader is shared", ["src/basicly/loop.py"])]
        },
    )

    recorded = _propose(monkeypatch, fake, _coupling_session(in_flight=("epic.1",)), tmp_path)

    assert recorded == (("epic.2", "epic.1", "blocks"),)


def test_only_a_coupling_record_proposes_an_edge(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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

    assert _propose(monkeypatch, fake, _coupling_session(in_flight=("epic.1",)), tmp_path) == ()
    assert fake.deps == []


def test_a_coupling_edge_is_recorded_once(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    existing = ({"id": "epic.1", "dependency_type": "blocks"},)
    fake = _FakeBr(
        _coupling_issues(deps=existing),
        comments={"epic.1": [_fold_marker("coupling", "both read the loader", ["epic.2"])]},
    )

    assert _propose(monkeypatch, fake, _coupling_session(in_flight=("epic.1",)), tmp_path) == ()
    assert fake.deps == []


def test_a_coupling_record_never_couples_its_source_to_itself(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _FakeBr(
        _coupling_issues(),
        comments={"epic.1": [_fold_marker("coupling", "note to self", ["epic.1"])]},
    )

    assert _propose(monkeypatch, fake, _coupling_session(in_flight=("epic.1",)), tmp_path) == ()
    assert fake.deps == []


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
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _FakeBr(
        _bundle_issues(),
        comments={
            "epic": [_fold_marker("decision", "keep the loader split", ["epic.1"])],
            "epic.2": [
                _fold_marker("coupling", "core file is shared", ["src/a/core.py"]),
                _fold_marker("fact", "docs only", ["docs/**"]),
            ],
        },
    )
    _install_br(monkeypatch, fake)

    bundle = supervise.build_bundle(tmp_path, "epic.1", known_ids=frozenset({"epic", "epic.2"}))

    assert [info.summary for info in bundle.folded] == [
        "keep the loader split",
        "core file is shared",
    ]
    assert bundle.prompt.startswith(loop.dispatch_prompt("epic.1"))
    assert "keep the loader split" in bundle.prompt
    assert "docs only" not in bundle.prompt


def test_build_bundle_treats_session_bead_ids_as_ids_not_globs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    issues = _bundle_issues()
    issues["epic.1"]["description"] = "Broad.\n\n## Scope\n\n- `**`\n"
    fake = _FakeBr(
        issues,
        comments={"epic": [_fold_marker("fact", "for the other lane", ["epic.2"])]},
    )
    _install_br(monkeypatch, fake)

    bundle = supervise.build_bundle(tmp_path, "epic.1", known_ids=frozenset({"epic", "epic.2"}))

    assert bundle.folded == ()


def test_build_bundle_sees_records_published_after_planning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    fake = _FakeBr(_bundle_issues())
    _install_br(monkeypatch, fake)
    known = frozenset({"epic", "epic.2"})

    before = supervise.build_bundle(tmp_path, "epic.1", known_ids=known)
    fake.comments["epic"] = [_fold_marker("constraint", "landed meanwhile", ["epic.1"])]
    after = supervise.build_bundle(tmp_path, "epic.1", known_ids=known)

    assert before.folded == ()
    assert [info.summary for info in after.folded] == ["landed meanwhile"]


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

    return decompose.SizingLookup(sizing, "" if sizing is not None else absence)


def _lane(issue_id: str, live: bool = True, status: str = "in_progress") -> supervise.AdoptedLane:
    return supervise.AdoptedLane(
        issue_id=issue_id,
        status=status,
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
    ranking = loop_state.Ranking(
        nodes=tuple(
            loop_state.RankedNode(
                rank=rank, score=rank * 10, issue_id=iid, title="", fallback_rank=rank
            )
            for rank, iid in ranked
        ),
        schema="tracker.scheduler.v1",
        fallback_sort="priority ASC, created_at ASC, id ASC",
    )
    monkeypatch.setattr(supervise.loop_state, "ready_ranking", lambda _r, *_a: ranking)
    monkeypatch.setattr(supervise.loop_state, "ready_ranked", lambda _r: ranking.nodes)
    monkeypatch.setattr(supervise.decisions, "has_pending", lambda _r, _i: False)
    monkeypatch.setattr(supervise, "_phase_of", lambda _r, _i: "build")
    monkeypatch.setattr(supervise, "_has_subtasks", lambda _r, _i: False)
    monkeypatch.setattr(supervise.policy, "spend_status", lambda *_a, **_k: _UNGRANTED)
    monkeypatch.setattr(supervise.decompose, "resolve_dispatch_sizing", lambda *_a: _lookup(None))
    monkeypatch.setattr(supervise.wip, "downstream_units", lambda *_a: ())


_UNGRANTED = policy.SpendStatus(grant=None, spent_tokens=0, halted=False)


def test_ready_lanes_filters_blocked_and_dead_and_orders_by_rank(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lanes = (_lane("epic.1"), _lane("epic.2"), _lane("epic.3", live=False), _lane("epic.4"))
    _patch_readiness(monkeypatch, blocked={"epic.2"}, ranked=((1, "epic.4"),))

    ready = supervise.ready_lanes(Path(), _session(*lanes))

    assert [lane.issue_id for lane in ready] == ["epic.4", "epic.1"]


def test_ready_lanes_take_no_runner_for_a_deferred_lane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    lanes = (_lane("epic.1"), _lane("epic.2", status="deferred"))
    _patch_readiness(monkeypatch, ranked=((1, "epic.1"), (2, "epic.2")))

    ready = supervise.ready_lanes(Path(), _session(*lanes))

    assert [lane.issue_id for lane in ready] == ["epic.1"]


def test_dispatch_lanes_runs_concurrently_up_to_the_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

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
        barrier.wait()
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
        detail="test",
    )


_MANUAL_SPEC = runner.RunnerSpec("manual", runner.HANDOFF)


def test_dispatch_lanes_heartbeats_while_runners_execute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    assert beats


def test_dispatch_lanes_lock_lost_cancels_lanes_not_yet_started(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
        release.set()
    assert started == ["epic.1"]


def test_dispatch_lanes_without_ready_lanes_is_a_no_op(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_readiness(monkeypatch, blocked={"epic.1"})
    assert supervise.dispatch_lanes(Path(), _session(_lane("epic.1"))) == ()


def _codex_events(tokens: int) -> str:

    event = {"type": "turn.completed", "usage": {"input_tokens": tokens, "output_tokens": 0}}
    return json.dumps(event)


def _codex() -> runner.RunnerSpec:
    return next(s for s in runner.BUILTIN_RUNNERS if s.name == "codex")


def _finished(spec: runner.RunnerSpec, stdout: str) -> runner.RunResult:
    return runner.RunResult(
        spec.name, tuple(spec.command), executed=True, returncode=0, stdout=stdout
    )


def _lane_issues() -> dict[str, dict]:
    return {
        "epic": _issue("epic", children=(("epic.1", "in_progress"),)),
        "epic.1": {
            "id": "epic.1",
            "status": "in_progress",
            "title": "Build the parser",
            "issue_type": "task",
            "priority": 0,
            "labels": ["phase-7", "determinism"],
            "acceptance_criteria": "- parses all three formats",
            "description": "Work.\n\n## Scope\n\n- `src/a/**`\n",
        },
    }


def _worker_fixture(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, stdout: str, returncode: int = 0
) -> tuple[_FakeBr, dict]:
    fake = _FakeBr(_lane_issues())
    _install_br(monkeypatch, fake)
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
    monkeypatch.setattr(supervise.decompose, "resolve_dispatch_sizing", lambda *_a: _lookup(None))
    return fake, seen


def test_dispatch_lane_records_the_scheduler_evidence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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
            policy="tracker.scheduler.v1",
        ),
    )

    assert captured["dispatch_rank"] == 2
    assert captured["scheduler_rank"] == 1
    assert captured["scheduler_fallback_rank"] == 3
    assert captured["scheduler_score"] == 45
    assert captured["scheduler_policy"] == "tracker.scheduler.v1"


def test_dispatch_lane_runs_as_the_build_persona(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    codex = _codex()
    _worker_fixture(monkeypatch, tmp_path, stdout=_codex_events(50_000))
    seen: dict = {}
    phases: list[str] = []

    def _run(spec, _prompt, _cwd, **kwargs):
        seen["kwargs"] = kwargs
        return runner.RunResult(
            spec.name,
            (spec.name,),
            executed=True,
            returncode=0,
            stdout=_codex_events(50_000),
            duration_s=0.1,
        )

    def _resolve(_repo_root, _spec, phase: str) -> str:
        phases.append(phase)
        return f"persona-for-{phase}"

    monkeypatch.setattr(supervise.runner, "run", _run)
    monkeypatch.setattr(supervise.roles, "resolve_role", _resolve)

    supervise._dispatch_lane(tmp_path, _session(_lane("epic.1")), _lane("epic.1"), codex, _sizing())

    assert phases == ["build"]
    assert seen["kwargs"]["role"] == "persona-for-build"


def test_dispatch_lane_records_the_order_when_br_never_ranked_the_lane(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

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
            dispatch_rank=1, node=None, policy="tracker.scheduler.v1"
        ),
    )

    assert captured["dispatch_rank"] == 1
    assert captured["scheduler_rank"] is None
    assert captured["scheduler_score"] is None
    assert captured["scheduler_policy"] == "tracker.scheduler.v1"


def test_dispatch_lanes_records_one_ranking_for_the_whole_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    assert {first.policy, second.policy} == {"tracker.scheduler.v1"}
    assert first.node is not None
    assert first.node.rank == 1


def test_dispatch_lane_green_path_meters_and_records(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    codex = _codex()
    tracker, seen = _worker_fixture(monkeypatch, tmp_path, stdout=_codex_events(50_000))

    outcome = supervise._dispatch_lane(
        tmp_path, _session(_lane("epic.1")), _lane("epic.1"), codex, _sizing()
    )

    assert seen["recorded"] == "epic.1"
    assert "epic.1" in seen["prompt"]
    assert outcome.occupancy == 50_000
    assert outcome.overrun is False
    assert outcome.detail == "finished; ready to land"
    assert tracker.created == []


def test_dispatch_lane_over_the_ceiling_lands_and_reports_both_numbers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    codex = _codex()
    tracker, _seen = _worker_fixture(monkeypatch, tmp_path, stdout=_codex_events(250_000))

    outcome = supervise._dispatch_lane(
        tmp_path, _session(_lane("epic.1")), _lane("epic.1"), codex, _sizing()
    )

    assert outcome.overrun is True
    assert outcome.detail == (
        "finished; ready to land; context occupancy 250000 tokens is over the "
        "240000-token ceiling (observed, not enforced)"
    )
    assert tracker.created == [], "no follow-up bead"
    assert tracker.comments == {}, "no [harness-overrun] marker either"


def test_dispatch_lane_surfaces_the_needs_input_sentinel(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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
    assert not sentinel.exists()


def test_dispatch_lane_without_worktree_record_asks_for_reprovision(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    codex = _codex()
    monkeypatch.setattr(supervise.worktree, "load_session", lambda *_a, **_k: None)

    outcome = supervise._dispatch_lane(
        tmp_path, _session(_lane("epic.1")), _lane("epic.1"), codex, _sizing()
    )

    assert outcome.result is None
    assert "re-provision" in outcome.detail


def test_dispatch_lane_failed_run_over_the_ceiling_keeps_its_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    tracker, _seen = _worker_fixture(
        monkeypatch, tmp_path, stdout=_codex_events(250_000), returncode=3
    )

    outcome = supervise._dispatch_lane(
        tmp_path, _session(_lane("epic.1")), _lane("epic.1"), _codex(), _sizing()
    )

    assert outcome.overrun is True
    assert tracker.created == []
    assert outcome.detail.startswith("runner exited 3; context occupancy 250000 tokens")


_WAL_CORRUPT = (
    'br comments list basicly-tcmy.20 --json failed: {"error": {"code": "DATABASE_ERROR", '
    '"message": "Database error: WAL file is corrupt: short read at frame 8: got 0, '
    'need 4120", "retryable": false}}'
)


def test_a_lane_that_dies_before_its_agent_starts_records_an_unstarted_dispatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _worker_fixture(monkeypatch, tmp_path, stdout="")
    monkeypatch.setattr(supervise.loop, "record_run", _never_recorded)
    monkeypatch.setattr(supervise, "build_bundle", _wal_corrupt)

    with pytest.raises(RuntimeError, match="WAL file is corrupt"):
        supervise._dispatch_lane(
            tmp_path, _session(_lane("epic.1")), _lane("epic.1"), _codex(), _sizing()
        )

    history = (run_record.load_run_records(tmp_path) or {})["epic.1"]
    assert [entry["outcome"] for entry in history] == [run_record.UNSTARTED]
    assert history[0]["estimated"] is True
    assert history[0]["tokens"] == len(_WAL_CORRUPT) // 4


def test_a_lane_that_dies_before_its_agent_starts_never_halts_the_grant(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    _worker_fixture(monkeypatch, tmp_path, stdout="")
    monkeypatch.setattr(supervise.loop, "record_run", _never_recorded)
    monkeypatch.setattr(supervise, "build_bundle", _wal_corrupt)

    with pytest.raises(RuntimeError, match="WAL file is corrupt"):
        supervise._dispatch_lane(
            tmp_path, _session(_lane("epic.1")), _lane("epic.1"), _codex(), _sizing()
        )

    status = policy.spend_status(
        tmp_path,
        "epic",
        grant=policy.Grant(level="L3", token_budget=60_000_000),
        ids=("epic.1",),
    )
    assert (status.halted, status.unmetered_dispatches) == (False, 0)
    assert status.remaining_tokens == 60_000_000


def _wal_corrupt(*_a: object, **_k: object) -> None:
    raise RuntimeError(_WAL_CORRUPT)


def _never_recorded(*_a: object, **_k: object) -> None:
    raise AssertionError("a dispatch that never started recorded a run")


def test_dispatch_lanes_contains_a_lane_failure_to_its_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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


def test_a_dispatch_lost_to_tracker_storage_is_marked_transient(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    _patch_readiness(monkeypatch)
    monkeypatch.setattr(supervise.runner, "select_runner", lambda *_a, **_k: _MANUAL_SPEC)
    contended = "another writer holds /repo/.basicly/ledger/.events.lock after 5.0s"

    def dispatch(_repo, _session, lane, _spec, _sizing, **_kw) -> supervise.LaneOutcome:
        if lane.issue_id == "epic.1":
            raise RuntimeError(f"br comments list basicly-x --json failed: {contended}")
        raise RuntimeError("br show basicly-y failed: no such issue")

    monkeypatch.setattr(supervise, "_dispatch_lane", dispatch)

    outcomes = supervise.dispatch_lanes(Path(), _session(_lane("epic.1"), _lane("epic.2")), cap=2)

    assert [o.transient for o in outcomes] == [True, False]


def test_parse_found_info_bounds_summary_and_detail() -> None:

    payload = json.dumps({"kind": "fact", "summary": "s" * 1000, "detail": "d" * 5000})
    info = supervise.parse_found_info(f"{supervise.INFO_MARKER} {payload}", "epic.1")
    assert info is not None
    assert len(info.summary) == 200
    assert len(info.detail) == 500


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
        salvaged=kw.pop("salvaged", False),
        detail=kw.pop("detail", "finished; ready to land"),
        provider_refusal=kw.pop("provider_refusal", ""),
        spend=kw.pop("spend", None),
    )


def _advance_result(issue_id: str, action: str, to_phase: str, detail: str = ""):
    return loop.AdvanceResult(issue_id, "build", to_phase, action, detail)


def test_route_green_lane_lands_and_ships_under_a_grant(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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
    grants: list[object] = []

    def fake_run(_r: Path, issue_id: str, **kwargs: object) -> list[loop.AdvanceResult]:
        grants.append(kwargs.get("grant_root"))
        return [_advance_result(issue_id, "tore-down", "done", "closed")]

    monkeypatch.setattr(supervise.loop, "run_until_blocked", fake_run)

    routed = supervise.route_outcomes(
        tmp_path, _session(_lane("epic.1")), (_executed_outcome("epic.1"),)
    )

    assert [r.route for r in routed] == ["shipped"]
    assert routed[0].progressed
    assert grants == ["epic"]


def test_route_green_lane_without_a_grant_queues_the_ship_checkpoint(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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
    return supervise.decisions.DecisionItem(
        decision_id=f"{issue}#abc", issue_id=issue, kind=kind, question="q"
    )


def test_route_failed_dispatch_retries_then_escalates_at_the_cap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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
    needs = _executed_outcome("epic.1", needs_fact="which db?", detail="needs input")
    stalled = _executed_outcome("epic.2", returncode=None, timed_out=True, detail="timed out")

    routed = supervise.route_outcomes(
        tmp_path, _session(_lane("epic.1"), _lane("epic.2")), (needs, stalled)
    )

    assert [r.route for r in routed] == ["decision", "decision"]
    assert not any(r.progressed for r in routed)


def test_route_salvaged_timeout_lands_so_the_verify_gate_judges_the_rescued_diff(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

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
    salvaged = _executed_outcome(
        "epic.1", returncode=None, timed_out=True, salvaged=True, detail="timed out; committed"
    )
    lost = _executed_outcome("epic.2", returncode=None, timed_out=True, detail="timed out")

    routed = supervise.route_outcomes(
        tmp_path, _session(_lane("epic.1"), _lane("epic.2")), (salvaged, lost)
    )

    assert [(r.issue_id, r.route) for r in routed] == [
        ("epic.1", "shipped"),
        ("epic.2", "decision"),
    ]


def test_a_salvaged_timeout_whose_gate_is_red_reworks_instead_of_landing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    monkeypatch.setattr(
        supervise.loop,
        "advance",
        lambda _r, issue_id, **_k: loop.AdvanceResult(
            issue_id, "build", "build", "blocked", "verify failed: pytest (rework 1/2)"
        ),
    )
    outcome = _executed_outcome(
        "epic.1", returncode=None, timed_out=True, salvaged=True, detail="timed out; committed"
    )

    routed = supervise.route_outcomes(tmp_path, _session(_lane("epic.1")), (outcome,))

    assert [r.route for r in routed] == ["rework"]
    assert "verify failed" in routed[0].detail


def test_a_salvaged_landing_that_fails_stops_the_pass_like_any_other(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    monkeypatch.setattr(
        supervise.loop,
        "advance",
        lambda _r, issue_id, **_k: loop.AdvanceResult(
            issue_id, "build", "build", "blocked", "merge commit rejected"
        ),
    )
    monkeypatch.setattr(supervise, "_landing_order", lambda _r, outcomes: list(outcomes))
    salvaged = _executed_outcome(
        "epic.1", returncode=None, timed_out=True, salvaged=True, detail="timed out; committed"
    )
    behind = _executed_outcome("epic.2")

    routed = supervise.route_outcomes(
        tmp_path, _session(_lane("epic.1"), _lane("epic.2")), (salvaged, behind)
    )

    assert [(r.issue_id, r.route) for r in routed] == [
        ("epic.1", "rework"),
        ("epic.2", "held"),
    ]
    assert "landing paused" in routed[1].detail


def test_route_handoff_stays_with_the_driving_agent(tmp_path: Path) -> None:
    handoff = supervise.LaneOutcome(
        issue_id="epic.1",
        runner_name="manual",
        result=runner.RunResult("manual", (), executed=False, handoff=True),
        needs_fact=None,
        occupancy=None,
        overrun=False,
        detail="handoff runner: work left to the driving agent",
    )
    routed = supervise.route_outcomes(tmp_path, _session(_lane("epic.1")), (handoff,))
    assert [r.route for r in routed] == ["handoff"]


def test_ready_lanes_skip_lanes_waiting_on_a_decision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_readiness(monkeypatch)
    monkeypatch.setattr(supervise.decisions, "has_pending", lambda _r, issue: issue == "epic.1")
    ready = supervise.ready_lanes(Path(), _session(_lane("epic.1"), _lane("epic.2")))
    assert [lane.issue_id for lane in ready] == ["epic.2"]


def test_dispatch_lane_timeout_queues_a_stall(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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
    assert "stopped on runner_timeout after" in outcome.detail
    assert outcome.result is not None and outcome.result.timed_out


def test_dispatch_lane_timeout_commits_the_worktree_and_still_queues_the_stall(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    codex = _codex()
    _br, _seen = _worker_fixture(monkeypatch, tmp_path, stdout="")
    monkeypatch.setattr(
        supervise.runner,
        "run",
        lambda spec, *_a, **_k: runner.RunResult(
            spec.name, (spec.name,), executed=True, timed_out=True
        ),
    )
    salvaged: list[tuple[Path, str, str]] = []

    def fake_salvage(cwd, bead, *, reason):
        salvaged.append((Path(cwd), bead, reason))
        return supervise.commit.Salvage("committed", "the worktree was committed as abc1234")

    monkeypatch.setattr(supervise.commit, "salvage", fake_salvage)
    queued: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        supervise.decisions,
        "enqueue",
        lambda _r, issue, kind, _q, detail="", **_k: (
            queued.append((issue, kind, detail)),
            decisions_item(issue, kind),
        )[1],
    )

    outcome = supervise._dispatch_lane(
        tmp_path, _session(_lane("epic.1")), _lane("epic.1"), codex, _sizing()
    )

    assert salvaged == [(tmp_path / "wt", "epic.1", "runner_timeout after 3600s")]
    assert outcome.salvaged is True
    assert "the worktree was committed as abc1234" in outcome.detail
    assert [(issue, kind) for issue, kind, _d in queued] == [("epic.1", "stall")]
    assert "abc1234" in queued[0][2], "the human triaging the kill is told what landed"


def test_a_timeout_the_salvage_could_not_commit_still_parks_the_lane(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    codex = _codex()
    _worker_fixture(monkeypatch, tmp_path, stdout="")
    monkeypatch.setattr(
        supervise.runner,
        "run",
        lambda spec, *_a, **_k: runner.RunResult(
            spec.name, (spec.name,), executed=True, timed_out=True
        ),
    )
    monkeypatch.setattr(
        supervise.commit,
        "salvage",
        lambda *_a, **_k: supervise.commit.Salvage("refused", "the salvage commit was rejected"),
    )
    monkeypatch.setattr(
        supervise.decisions,
        "enqueue",
        lambda _r, issue, kind, *_a, **_k: decisions_item(issue, kind),
    )

    outcome = supervise._dispatch_lane(
        tmp_path, _session(_lane("epic.1")), _lane("epic.1"), codex, _sizing()
    )

    assert outcome.salvaged is False
    assert "the salvage commit was rejected" in outcome.detail


def test_route_landing_rework_block_is_retriable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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


def _blocked_landing(
    issue_id: str, status: str, conflicts: tuple[str, ...] = (), *, escalated: bool = False
) -> loop.AdvanceResult:
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
) -> tuple[list[tuple[str, str]], _FakeBr]:

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

    fake = _FakeBr({bead: {"id": bead, "description": ""} for bead in scopes})
    _install_br(monkeypatch, fake)
    monkeypatch.setattr(policy, "_write", fake)

    def try_run_br(_r, args):
        if args[:2] == ["dep", "add"]:
            couplings.append((args[2], args[3]))
            return None
        return fake(_r, args)

    fake_tracker.install(monkeypatch, try_run_br)
    return couplings, fake


def test_route_bounces_a_collided_lane_and_lands_the_rest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    couplings, _ = _patch_collision_pass(
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

    assert [r.route for r in routed] == ["merged", "bounced", "merged"]
    assert couplings == [("epic.1", "epic.2")]
    assert "coupling recorded on epic.1" in routed[1].detail
    assert supervise.should_continue(routed) is True


def test_route_records_the_same_coupling_edge_under_either_completion_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    scopes = {"epic.1": ("src/shared.py",), "epic.2": ("src/*.py",)}

    def run(collides: str, order: tuple[str, ...]) -> list[tuple[str, str]]:
        couplings, _ = _patch_collision_pass(monkeypatch, collides=collides, scopes=scopes)
        outcomes = tuple(_executed_outcome(issue_id) for issue_id in order)
        supervise.route_outcomes(
            tmp_path, _session(*(_lane(o.issue_id) for o in outcomes)), outcomes
        )
        return couplings

    forward = run("epic.2", ("epic.1", "epic.2"))
    reversed_ = run("epic.1", ("epic.2", "epic.1"))

    assert forward == [("epic.1", "epic.2")]
    assert reversed_ == forward


def test_route_attributes_a_bounce_against_a_lane_that_landed_after_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    couplings, _ = _patch_collision_pass(
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
    couplings, _ = _patch_collision_pass(
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
    monkeypatch.setattr(
        supervise.loop,
        "advance",
        lambda _r, issue_id, **_k: _blocked_landing(issue_id, "rebase-conflicts", ("src/a.py",)),
    )
    monkeypatch.setattr(supervise, "_landing_order", lambda _r, outcomes: list(outcomes))
    _install_br(monkeypatch, _FakeBr({}))
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
    monkeypatch.setattr(
        supervise.loop,
        "advance",
        lambda _r, issue_id, **_k: _blocked_landing(
            issue_id, "merge-conflicts", ("src/a.py",), escalated=True
        ),
    )
    monkeypatch.setattr(supervise, "_landing_order", lambda _r, outcomes: list(outcomes))
    monkeypatch.setattr(supervise.merge, "record_coupling", lambda *_a: None)
    _install_br(monkeypatch, _FakeBr({}))

    routed = supervise.route_outcomes(
        tmp_path, _session(_lane("epic.1")), (_executed_outcome("epic.1"),)
    )

    assert [r.route for r in routed] == ["decision"]
    assert supervise.should_continue(routed) is False


def _briefs_on(fake: _FakeBr, bead: str) -> tuple[supervise.FoundInfo, ...]:
    parsed = (supervise.parse_found_info(text, bead) for text in fake.comments.get(bead, []))
    return tuple(info for info in parsed if info is not None)


def test_a_bounced_lane_is_briefed_with_the_conflicting_paths_and_both_sides(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    _, fake = _patch_collision_pass(
        monkeypatch,
        collides="epic.2",
        scopes={"epic.1": ("src/shared.py",), "epic.2": ("src/shared.py",)},
    )
    outcomes = tuple(_executed_outcome(f"epic.{n}") for n in (1, 2))

    routed = supervise.route_outcomes(
        tmp_path, _session(*(_lane(o.issue_id) for o in outcomes)), outcomes
    )

    assert [r.route for r in routed] == ["merged", "bounced"]
    (brief,) = _briefs_on(fake, "epic.2")
    assert brief.kind == "coupling" and brief.affects == ("epic.2",)
    assert "src/shared.py" in brief.summary
    assert "epic.1" in brief.summary
    assert "already on this lane's branch" in brief.detail
    assert "as they now stand on the base" in brief.detail
    prompt = supervise.build_bundle(tmp_path, "epic.2").prompt
    assert "src/shared.py" in prompt and "epic.1" in prompt
    assert _briefs_on(fake, "epic.1") == ()


def test_a_bounce_brief_names_the_paths_when_no_landing_is_attributable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    couplings, fake = _patch_collision_pass(
        monkeypatch,
        collides="epic.2",
        scopes={"epic.1": ("docs/**",), "epic.2": ("src/shared.py",)},
    )
    outcomes = tuple(_executed_outcome(f"epic.{n}") for n in (1, 2))

    supervise.route_outcomes(tmp_path, _session(*(_lane(o.issue_id) for o in outcomes)), outcomes)

    assert couplings == []
    (brief,) = _briefs_on(fake, "epic.2")
    assert "src/shared.py" in brief.summary and "epic.1" not in brief.summary


def test_conflict_signature_is_free_of_the_order_git_reported_the_paths_in() -> None:
    forward = merge.MergeResult("epic.1", "merge-conflicts", "d", conflicts=("b.py", "a.py"))
    reversed_ = merge.MergeResult("epic.1", "merge-conflicts", "d", conflicts=("a.py", "b.py"))

    assert supervise.conflict_signature(forward) == supervise.conflict_signature(reversed_)
    other = merge.MergeResult("epic.1", "rebase-conflicts", "d", conflicts=("a.py", "b.py"))
    assert supervise.conflict_signature(other) != supervise.conflict_signature(forward)


def _bounce_twice(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    second: tuple[str, ...],
) -> tuple[list[supervise.RoutedOutcome], list[tuple[str, str]]]:

    fake = _FakeBr({"epic.1": {"id": "epic.1", "description": ""}})
    allowances: list[tuple[str, str]] = []
    monkeypatch.setattr(supervise, "_landing_order", lambda _r, outcomes: list(outcomes))
    monkeypatch.setattr(supervise.merge, "record_coupling", lambda *_a: None)
    monkeypatch.setattr(
        supervise.decisions,
        "enqueue",
        lambda _r, issue, kind, *_a, **_k: decisions_item(issue, kind),
    )
    monkeypatch.setattr(
        supervise.policy,
        "grant_rework_allowance",
        lambda _r, issue, gate: allowances.append((issue, gate)) or 0,
    )
    _install_br(monkeypatch, fake)
    monkeypatch.setattr(policy, "_write", fake)

    routes: list[supervise.RoutedOutcome] = []
    for conflicts in (("src/shared.py",), second):
        monkeypatch.setattr(
            supervise.loop,
            "advance",
            lambda _r, issue_id, _c=conflicts, **_k: _blocked_landing(
                issue_id, "merge-conflicts", _c
            ),
        )
        routes.extend(
            supervise.route_outcomes(
                tmp_path, _session(_lane("epic.1")), (_executed_outcome("epic.1"),)
            )
        )
    return routes, allowances


def test_a_second_identical_bounce_escalates_without_charging_the_rework_cap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    routes, allowances = _bounce_twice(monkeypatch, tmp_path, second=("src/shared.py",))

    assert [r.route for r in routes] == ["bounced", "decision"]
    assert allowances == [("epic.1", merge.MERGE_GATE)]
    assert "identically twice" in routes[1].detail and "src/shared.py" in routes[1].detail


def test_a_bounce_on_different_paths_is_not_a_repeat(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    routes, allowances = _bounce_twice(monkeypatch, tmp_path, second=("src/other.py",))

    assert [r.route for r in routes] == ["bounced", "bounced"]
    assert allowances == []


def test_the_bounce_signature_is_stored_by_the_shared_finding_set_mechanism(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    fake = _FakeBr({"epic.1": {"id": "epic.1", "description": ""}})
    monkeypatch.setattr(supervise, "_landing_order", lambda _r, outcomes: list(outcomes))
    monkeypatch.setattr(supervise.merge, "record_coupling", lambda *_a: None)
    monkeypatch.setattr(
        supervise.loop,
        "advance",
        lambda _r, issue_id, **_k: _blocked_landing(issue_id, "merge-conflicts", ("src/a.py",)),
    )
    _install_br(monkeypatch, fake)
    monkeypatch.setattr(policy, "_write", fake)

    supervise.route_outcomes(tmp_path, _session(_lane("epic.1")), (_executed_outcome("epic.1"),))

    signatures = [c for c in fake.comments["epic.1"] if c.startswith(policy.FINDING_SET_MARKER)]
    assert signatures == [
        f"{policy.FINDING_SET_MARKER} gate={merge.MERGE_GATE} verdict={policy.PROGRESSING} "
        'findings=["src/a.py", "status=merge-conflicts"]'
    ]


def test_a_bounce_survives_a_tracker_that_refuses_the_signature(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    monkeypatch.setattr(supervise, "_landing_order", lambda _r, outcomes: list(outcomes))
    monkeypatch.setattr(supervise.merge, "record_coupling", lambda *_a: None)
    monkeypatch.setattr(
        supervise.loop,
        "advance",
        lambda _r, issue_id, **_k: _blocked_landing(issue_id, "merge-conflicts", ("src/a.py",)),
    )
    _install_br(monkeypatch, _FakeBr({}))

    def refuse(*_a, **_k):
        raise RuntimeError("br comments add failed")

    monkeypatch.setattr(policy, "record_finding_set", refuse)

    routed = supervise.route_outcomes(
        tmp_path, _session(_lane("epic.1")), (_executed_outcome("epic.1"),)
    )

    assert [r.route for r in routed] == ["bounced"]


def test_route_still_holds_later_lanes_when_a_gate_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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
    assert supervise.carried_forward(routed) == frozenset({"epic.1"})
    assert supervise.should_continue(routed) is True


def test_route_holds_every_lane_a_siblings_declaration_invalidated_the_gate_for(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    monkeypatch.setattr(
        supervise.loop,
        "advance",
        lambda _r, issue_id, **_k: _blocked_landing(issue_id, merge.VERIFY_FOREIGN),
    )
    monkeypatch.setattr(supervise, "_landing_order", lambda _r, outcomes: list(outcomes))
    charged: list = []
    monkeypatch.setattr(supervise.policy, "record_rework", lambda *a, **_k: charged.append(a) or 1)
    outcomes = (_executed_outcome("epic.1"), _executed_outcome("epic.2"))

    routed = supervise.route_outcomes(
        tmp_path, _session(*(_lane(o.issue_id) for o in outcomes)), outcomes
    )

    assert [r.route for r in routed] == ["held", "held"]
    assert charged == []
    assert supervise.carried_forward(routed) == frozenset({"epic.1", "epic.2"})


def test_route_still_charges_a_lane_for_a_defect_in_its_own_diff(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    monkeypatch.setattr(
        supervise.loop,
        "advance",
        lambda _r, issue_id, **_k: _blocked_landing(issue_id, "verify-failed"),
    )
    monkeypatch.setattr(supervise, "_landing_order", lambda _r, outcomes: list(outcomes))
    outcomes = (_executed_outcome("epic.1"),)

    routed = supervise.route_outcomes(
        tmp_path, _session(*(_lane(o.issue_id) for o in outcomes)), outcomes
    )

    assert [r.route for r in routed] == ["rework"]
    assert supervise.carried_forward(routed) == frozenset()


class _WtBinding:
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
    monkeypatch.setattr(
        supervise.merge,
        "record_coupling",
        lambda *_a: pytest.fail("the pre-empt must not record a gating dependency edge"),
    )
    return seen


def test_route_cancels_a_lane_whose_merge_the_landing_broke_and_lands_the_rest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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
    assert seen["landed"] == ["epic.1", "epic.3"]
    assert "epic.1" in routed[1].detail
    assert seen["rework"] == [("epic.2", supervise.DISPATCH_GATE)]
    assert "dispatch rework 1/2" in routed[1].detail
    assert supervise.should_continue(routed) is True
    assert supervise.carried_forward(routed) == frozenset()


def test_route_publishes_the_cancellation_for_the_lanes_next_prompt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

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
    seen = _patch_preempt_pass(
        monkeypatch,
        conflicts={"epic.2": ("src/shared.py",)},
        changed=("src/shared.py",),
        known_rework=1,
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
    seen = _patch_preempt_pass(
        monkeypatch,
        conflicts={"epic.2": (".basicly/ledger/events-0001.jsonl",)},
        changed=(".basicly/ledger/events-0001.jsonl",),
    )
    outcomes = (_executed_outcome("epic.1"), _executed_outcome("epic.2"))

    routed = supervise.route_outcomes(
        tmp_path, _session(*(_lane(o.issue_id) for o in outcomes)), outcomes
    )

    assert [r.route for r in routed] == ["merged", "merged"]
    assert seen["landed"] == ["epic.1", "epic.2"] and seen["rework"] == []


def test_route_does_not_cancel_a_lane_no_landing_this_pass_can_explain(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

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
    seen = _patch_preempt_pass(
        monkeypatch,
        conflicts={"epic.2": ("src/shared.py",)},
        changed=("src/shared.py",),
    )
    monkeypatch.setattr(
        supervise, "ready_lanes", lambda _r, _s, **_k: (_lane("epic.1"), _lane("epic.2"))
    )
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

    deps = {"epic.1": frozenset({"epic.2"}), "epic.2": frozenset(), "epic.3": frozenset()}
    monkeypatch.setattr(supervise.merge, "blocking_dependencies", lambda _r, bead: deps[bead])
    outcomes = tuple(_executed_outcome(f"epic.{n}") for n in (1, 2, 3))

    ordered = [o.issue_id for o in supervise._landing_order(Path(), outcomes)]

    assert ordered.index("epic.2") < ordered.index("epic.1")
    assert ordered == ["epic.2", "epic.3", "epic.1"]


def test_carried_forward_carries_only_the_held_lanes() -> None:
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

    assert landed == ["epic.1", "epic.2"]
    assert [(r.issue_id, r.route) for r in routed] == [("epic.1", "merged"), ("epic.2", "merged")]


def test_a_carried_lane_that_fails_to_land_is_not_carried_again(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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
    _patch_readiness(monkeypatch, ranked=((1, "epic.1"),))
    monkeypatch.setattr(supervise, "_phase_of", lambda _r, _i: "verify")
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

    assert advanced == ["epic.1"]
    assert [r.route for r in routed] == ["shipped"]


def test_ready_lanes_skip_a_lane_with_subtask_beads(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_readiness(monkeypatch, ranked=((1, "epic.1"), (2, "epic.2")))
    monkeypatch.setattr(supervise, "_has_subtasks", lambda _r, issue: issue == "epic.1")

    ready = supervise.ready_lanes(Path(), _session(_lane("epic.1"), _lane("epic.2")))

    assert [lane.issue_id for lane in ready] == ["epic.2"]


def test_has_subtasks_counts_closed_subtasks_too(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    issues = {
        "epic.1": _issue("epic.1", children=(("epic.1.1", "closed"),)),
        "epic.2": _issue("epic.2"),
    }
    _install_br(monkeypatch, _FakeBrShow(issues))
    assert supervise._has_subtasks(tmp_path, "epic.1") is True
    assert supervise._has_subtasks(tmp_path, "epic.2") is False


def test_advance_parked_drives_a_mini_loop_lane(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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
    monkeypatch.setattr(supervise, "_phase_of", lambda _r, _i: "verify")
    monkeypatch.setattr(supervise.decisions, "has_pending", lambda _r, _i: True)
    monkeypatch.setattr(
        supervise.loop,
        "run_until_blocked",
        lambda *_a, **_k: pytest.fail("must not advance a lane awaiting judgment"),
    )
    assert supervise.advance_parked(tmp_path, _session(_lane("epic.1"))) == ()


def test_heartbeat_thread_keeps_the_lock_fresh_and_captures_loss(tmp_path: Path) -> None:
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
        hb.check()
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


def _attach_br(monkeypatch: pytest.MonkeyPatch, fake: _FakeBr) -> None:

    monkeypatch.setattr(policy, "_write", fake)
    fake_tracker.install(monkeypatch, fake)


def _grant_comment(level: str, budget: int | None = None) -> str:
    text = f"{policy.MARKER} grant level={level}"
    return text if budget is None else f"{text} budget={budget}"


def test_observe_reports_the_holder_lanes_decisions_and_spend(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _FakeBr(
        {
            "epic": _issue("epic", children=(("epic.1", "in_progress"), ("epic.2", "closed"))),
            "epic.1": _issue(
                "epic.1", "in_progress", external_ref="worktree:epic-1:harness/epic-1"
            ),
            "epic.2": _issue("epic.2", "closed"),
        },
        comments={"epic": [_grant_comment("L2", 5000)]},
    )
    _attach_br(monkeypatch, fake)
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
            last_run_at=view.lanes[0].last_run_at,
            last_tokens=1200,
        ),
    )
    assert [item.decision_id for item in view.pending_decisions] == [queued.decision_id]
    assert (view.grant_level, view.token_budget, view.spent_tokens) == ("L2", 5000, 1200)
    assert (view.human_wait_s, view.delegated_wait_s, view.dispatch_s) == (1_800, 0, 12.5)


def test_observe_an_unsupervised_root_is_a_valid_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _FakeBr({"task": _issue("task")})
    _attach_br(monkeypatch, fake)
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
    fake = _FakeBr({"epic": _issue("epic")})
    _attach_br(monkeypatch, fake)
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
    fake = _FakeBr({"epic": _issue("epic")})
    _attach_br(monkeypatch, fake)
    _fake_sessions(monkeypatch, set())
    supervise.acquire(tmp_path, "other:live", "other-epic")

    view = supervise.observe(tmp_path, "epic")

    assert view.holder is not None
    assert view.holder.root_issue == "other-epic"
    assert (view.holder_on_this_root, view.holder_stale, view.supervised) == (False, False, False)


def test_observe_never_touches_the_lock(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = _FakeBr({"epic": _issue("epic")})
    _attach_br(monkeypatch, fake)
    _fake_sessions(monkeypatch, set())
    lock = supervise.acquire(tmp_path, "epic:live", "epic")
    before = (lock.read_text(encoding="utf-8"), lock.stat().st_mtime)

    supervise.observe(tmp_path, "epic")

    assert (lock.read_text(encoding="utf-8"), lock.stat().st_mtime) == before
    lock.unlink()
    supervise.observe(tmp_path, "epic")
    assert not lock.exists()


def _granted(level: str, budget: int | None, spent: int) -> policy.SpendStatus:
    halted = budget is not None and spent >= budget
    return policy.SpendStatus(
        grant=policy.Grant(level=level, token_budget=budget),
        spent_tokens=spent,
        halted=halted,
        detail=f"{level} grant token_budget spent ({spent}/{budget} tokens)" if halted else "",
    )


def test_dispatch_lanes_still_starts_a_lane_when_the_grant_budget_is_spent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    _patch_readiness(monkeypatch, ranked=((1, "epic.1"),))
    monkeypatch.setattr(supervise.runner, "select_runner", lambda *_a, **_k: _MANUAL_SPEC)
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

    assert outcomes, "a spent budget still refused to start the lane"
    assert queued == [], "and it still enqueued an escalation nobody has to answer"


def test_dispatch_lanes_admits_a_grant_still_inside_its_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

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
    _patch_readiness(monkeypatch, ranked=((1, "epic.1"),))
    monkeypatch.setattr(supervise.runner, "select_runner", lambda *_a, **_k: _MANUAL_SPEC)
    monkeypatch.setattr(
        supervise.policy, "spend_status", lambda *_a, **_k: _granted("L3", 100, 100)
    )
    monkeypatch.setattr(supervise.decisions, "enqueue", lambda *_a, **_k: None)

    assert supervise.dispatch_lanes(Path(), _session(_lane("epic.1")))


def _delegation_env(
    monkeypatch: pytest.MonkeyPatch,
    *,
    pending: tuple[decisions.DecisionItem, ...],
    outcomes: dict[str, object],
) -> list[str]:
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
    offered = _delegation_env(monkeypatch, pending=(_item("epic.1#a", "needs-input"),), outcomes={})

    assert (
        supervise.delegate_decisions(
            Path(), _session(_lane("epic.1")), admission=_granted(level, None, 0)
        )
        == ()
    )
    assert offered == []


def test_delegate_decisions_needs_any_grant_at_all(monkeypatch: pytest.MonkeyPatch) -> None:
    offered = _delegation_env(monkeypatch, pending=(_item("epic.1#a", "needs-input"),), outcomes={})

    assert (
        supervise.delegate_decisions(Path(), _session(_lane("epic.1")), admission=_UNGRANTED) == ()
    )
    assert offered == []


def test_delegate_decisions_reports_an_abstention_as_still_the_humans(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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


def test_build_bundle_folds_the_lanes_answered_questions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    fake = _FakeBr({"epic.1": _issue("epic.1")})
    _install_br(monkeypatch, fake)
    monkeypatch.setattr(decisions, "_notify", lambda *_a, **_k: None)
    item = decisions.enqueue(tmp_path, "epic.1", "needs-input", "which db?")
    decisions.answer(tmp_path, item.decision_id, "postgres", by="human")
    still_open = decisions.enqueue(tmp_path, "epic.1", "escalation", "retry or park?")

    bundle = supervise.build_bundle(tmp_path, "epic.1")

    assert [i.decision_id for i in bundle.answers] == [item.decision_id]
    assert "which db? → postgres (answered by human)" in bundle.prompt
    assert "do not re-ask" in bundle.prompt
    assert still_open.question not in bundle.prompt


def test_build_bundle_omits_the_answers_section_when_there_are_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _FakeBr({"epic.1": _issue("epic.1")})
    _install_br(monkeypatch, fake)

    bundle = supervise.build_bundle(tmp_path, "epic.1")

    assert bundle.answers == ()
    assert bundle.prompt == loop.dispatch_prompt("epic.1")


def test_lane_activity_changes_on_a_commit_and_on_a_file_write(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "t"], check=True)
    (tmp_path / "a.txt").write_text("one", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "first"], check=True)

    idle = supervise.lane_activity(tmp_path)
    assert supervise.lane_activity(tmp_path) == idle

    (tmp_path / "b.txt").write_text("two", encoding="utf-8")
    wrote = supervise.lane_activity(tmp_path)
    assert wrote != idle

    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "second"], check=True)
    assert supervise.lane_activity(tmp_path) not in (idle, wrote)


def test_lane_activity_never_raises_on_a_path_that_is_not_a_repo(tmp_path: Path) -> None:
    assert supervise.lane_activity(tmp_path / "nope")


def test_flag_stalled_lane_queues_one_item_and_names_the_hard_kill(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _FakeBr({"epic.1": _issue("epic.1"), "epic.2": _issue("epic.2")})
    _install_br(monkeypatch, fake)
    monkeypatch.setattr(decisions, "_notify", lambda *_a, **_k: None)

    first = supervise.flag_stalled_lane(tmp_path, "epic.1", 900.0, 3600.0)
    again = supervise.flag_stalled_lane(tmp_path, "epic.1", 900.0, 3600.0)

    assert first.decision_id == again.decision_id
    assert len(fake.comments["epic.1"]) == 1
    assert first.kind == "stall"
    assert "900s" in first.detail
    assert "3600s" in first.detail
    brief = supervise.flag_stalled_lane(tmp_path, "epic.2", 0.2, 3600.0)
    assert "0.2s" in brief.detail
    assert "0s;" not in brief.detail
    assert first.pending


def test_a_stall_flag_is_retired_once_its_dispatch_has_ended(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    fake = _FakeBr({"epic.1": _issue("epic.1")})
    _install_br(monkeypatch, fake)
    monkeypatch.setattr(decisions, "_notify", lambda *_a, **_k: None)
    flagged = supervise.flag_stalled_lane(tmp_path, "epic.1", 900.0, 3600.0)
    assert flagged.pending
    assert decisions.has_pending(tmp_path, "epic.1")

    disposed = supervise.resolve_stall_flag(tmp_path, "epic.1")

    assert disposed == (flagged.decision_id,)
    assert not decisions.has_pending(tmp_path, "epic.1")
    item = decisions.get(tmp_path, flagged.decision_id)
    assert item is not None and not item.pending
    assert item.answered_by == decisions.ENGINE_BY


def test_retiring_a_stall_flag_is_idempotent_and_leaves_other_items_alone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    fake = _FakeBr({"epic.1": _issue("epic.1")})
    _install_br(monkeypatch, fake)
    monkeypatch.setattr(decisions, "_notify", lambda *_a, **_k: None)
    supervise.flag_stalled_lane(tmp_path, "epic.1", 900.0, 3600.0)
    hard_kill = decisions.enqueue(
        tmp_path, "epic.1", "stall", "runner claude hit runner_timeout (3600s): retry?"
    )

    assert len(supervise.resolve_stall_flag(tmp_path, "epic.1")) == 1
    assert supervise.resolve_stall_flag(tmp_path, "epic.1") == ()

    still_open = decisions.get(tmp_path, hard_kill.decision_id)
    assert still_open is not None and still_open.pending


def test_an_engine_retired_stall_flag_is_not_charged_as_human_wait(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _FakeBr({"epic.1": _issue("epic.1")})
    _install_br(monkeypatch, fake)
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

    fake = _FakeBr({"epic.1": _issue("epic.1")})
    _install_br(monkeypatch, fake)
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
    monkeypatch.setattr(
        supervise.decompose,
        "resolve_dispatch_sizing",
        lambda *_a: _lookup(_dispatch_sizing(20_000)),
    )
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

    stalls = [i for i in decisions.items_on(tmp_path, "epic.1") if i.kind == "stall"]
    assert len(stalls) == 1
    assert "may be stuck" in stalls[0].question
    assert outcome.result is not None
    assert outcome.result.returncode == 0
    assert outcome.result.stdout == "done"
    assert not stalls[0].pending
    assert stalls[0].answered_by == decisions.ENGINE_BY
    assert not decisions.has_pending(tmp_path, "epic.1")


def test_dispatch_lane_records_its_forecast_beside_its_actual(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

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


def test_the_lane_dispatch_records_a_write_phase_and_a_seeded_factor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

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

    assert captured["phase"] == run_record.LANE_PHASE
    assert run_record.is_write_phase(captured["phase"])
    assert captured["build_factor_source"] == decompose.BUILD_FACTOR_SEED


def test_a_lane_that_died_records_the_scope_it_was_sized_on(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    codex = _codex()
    _worker_fixture(monkeypatch, tmp_path, stdout=_codex_events(50_000), returncode=143)
    captured: dict = {}
    monkeypatch.setattr(supervise.loop, "record_run", lambda *_a, **kw: captured.update(kw))
    monkeypatch.setattr(
        supervise.decompose,
        "resolve_dispatch_sizing",
        lambda *_a: _lookup(_dispatch_sizing(21_000, 9_000)),
    )

    outcome = supervise._dispatch_lane(
        tmp_path, _session(_lane("epic.1")), _lane("epic.1"), codex, _sizing()
    )

    assert outcome.result is not None and outcome.result.returncode == 143
    assert captured["scope_tokens"] == 9_000
    assert captured["forecast_tokens"] == 21_000


def test_an_unsizeable_lane_records_the_bound_it_was_gated_on(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    codex = _codex()
    _worker_fixture(monkeypatch, tmp_path, stdout=_codex_events(50_000))
    captured: dict = {}
    monkeypatch.setattr(supervise.loop, "record_run", lambda *_a, **kw: captured.update(kw))
    monkeypatch.setattr(
        supervise.decompose,
        "resolve_dispatch_sizing",
        lambda *_a: _lookup(None, decompose.SCOPE_UNDECLARED),
    )
    monkeypatch.setattr(
        supervise.decompose, "unsized_lane_tokens", lambda *_a: (16_002_352, "measured")
    )

    supervise._dispatch_lane(tmp_path, _session(_lane("epic.1")), _lane("epic.1"), codex, _sizing())

    assert captured["forecast_spend_tokens"] == 16_002_352
    assert "forecast_tokens" not in captured
    assert captured["forecast_source"] == "assumed:measured"


def test_admit_working_set_never_checked_a_bead_that_declares_no_scope(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    fake_tracker.install(
        monkeypatch,
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
    assert admission.refused is False
    assert admission.violation is not None
    assert "never checked" in admission.violation
    assert "8000..64000" in admission.violation


def test_admit_working_set_stays_indeterminate_when_the_bead_cannot_be_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    monkeypatch.setattr(tracker, "read_record", lambda *_a, **_k: None)

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

    spawned = _band_fixture(
        monkeypatch, tmp_path, _dispatch_sizing(100_000), spawn_is_a_failure=True
    )

    outcome = _dispatch_epic1(tmp_path)

    assert spawned == []
    assert outcome.refused is True
    assert outcome.result is None
    assert "100000" in outcome.detail
    assert "64000" in outcome.detail
    assert "refused" in outcome.detail
    items = decisions.items_on(tmp_path, "epic.1")
    assert [(i.kind, i.pending) for i in items] == [("escalation", True)]
    assert decisions.has_pending(tmp_path, "epic.1") is True
    assert items[0].decision_id in outcome.detail


def test_dispatch_escalates_but_still_runs_a_lane_below_the_floor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    spawned = _band_fixture(monkeypatch, tmp_path, _dispatch_sizing(3_000))

    outcome = _dispatch_epic1(tmp_path)

    assert spawned == ["codex"]
    assert outcome.refused is False
    assert outcome.detail == "finished; ready to land"
    items = decisions.items_on(tmp_path, "epic.1")
    assert len(items) == 1
    assert items[0].kind == "escalation"
    assert "3000" in items[0].detail
    assert "8000" in items[0].detail
    assert items[0].answered_by == decisions.ENGINE_BY
    assert decisions.has_pending(tmp_path, "epic.1") is False


def test_dispatch_runs_an_in_band_lane_without_a_word(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    spawned = _band_fixture(monkeypatch, tmp_path, _dispatch_sizing(20_000))

    outcome = _dispatch_epic1(tmp_path)

    assert spawned == ["codex"]
    assert outcome.refused is False
    assert decisions.items_on(tmp_path, "epic.1") == ()


def test_dispatch_admits_a_lane_whose_working_set_cannot_be_estimated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    spawned = _band_fixture(monkeypatch, tmp_path, None, absence=decompose.SCOPE_UNDECLARED)

    outcome = _dispatch_epic1(tmp_path)

    assert spawned == ["codex"]
    assert outcome.refused is False


def test_dispatch_does_not_silently_admit_a_lane_that_declares_no_scope(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    spawned = _band_fixture(monkeypatch, tmp_path, None, absence=decompose.SCOPE_UNDECLARED)

    outcome = _dispatch_epic1(tmp_path)

    assert spawned == ["codex"]
    assert outcome.refused is False
    items = decisions.items_on(tmp_path, "epic.1")
    assert len(items) == 1
    assert items[0].kind == "escalation"
    assert items[0].question == working_set.UNSIZED_QUESTION
    assert "never" in items[0].detail
    assert "8000..64000" in items[0].detail
    assert items[0].answered_by == decisions.ENGINE_BY
    assert decisions.has_pending(tmp_path, "epic.1") is False


def test_dispatch_admits_a_lane_whose_estimator_cannot_be_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    def unreadable(*_a: object) -> decompose.SizingLookup:
        raise RuntimeError("br export failed")

    spawned = _band_fixture(monkeypatch, tmp_path, None)
    monkeypatch.setattr(supervise.decompose, "resolve_dispatch_sizing", unreadable)

    assert _dispatch_epic1(tmp_path).refused is False
    assert spawned == ["codex"]
    assert decisions.items_on(tmp_path, "epic.1") == ()


def test_route_a_refused_lane_to_the_queue_rather_than_a_retry(tmp_path: Path) -> None:

    refused = supervise.LaneOutcome(
        issue_id="epic.1",
        runner_name="codex",
        result=None,
        needs_fact=None,
        occupancy=None,
        overrun=False,
        detail="dispatch refused before it started",
        refused=True,
    )

    routed = supervise.route_outcomes(tmp_path, _session(_lane("epic.1")), (refused,))

    assert [r.route for r in routed] == ["decision"]
    assert supervise.should_continue(routed) is False


def _forecast(tokens: int | None) -> decompose.SpendForecast:

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


def test_pass_warns_when_its_forecast_exceeds_the_remaining_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    _patch_readiness(monkeypatch, ranked=((1, "epic.1"), (2, "epic.2")))
    queued = _pass_fixture(
        monkeypatch,
        sizings={"epic.1": _dispatch_sizing(20_000), "epic.2": _dispatch_sizing(30_000)},
        forecasts={"epic.1": 4_000, "epic.2": 4_000},
    )
    said: list[str] = []

    outcomes = supervise.dispatch_lanes(
        Path(),
        _session(_lane("epic.1"), _lane("epic.2")),
        admission=_granted("L3", 10_000, 5_000),
        report=said.append,
    )

    assert outcomes, "an over-forecast pass was still refused"
    assert queued == [], "and it still enqueued a refusal"
    detail = "\n".join(said)
    assert "8000" in detail
    assert "5000" in detail
    assert "epic.1" in detail and "epic.2" in detail


def test_no_module_level_name_in_supervise_is_bound_twice() -> None:

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


def test_pass_is_admitted_when_its_forecast_fits_the_remainder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

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


def test_pass_forecast_ignores_a_lane_the_band_already_refuses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

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

    assert [o.issue_id for o in outcomes] == ["epic.1", "epic.2"]
    assert queued == []


def test_pass_names_the_lanes_it_could_not_forecast(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    _patch_readiness(monkeypatch, ranked=((1, "epic.1"), (2, "epic.2")))
    _pass_fixture(
        monkeypatch,
        sizings={"epic.1": _dispatch_sizing(20_000), "epic.2": None},
        forecasts={"epic.1": 9_000},
    )
    said: list[str] = []

    supervise.dispatch_lanes(
        Path(),
        _session(_lane("epic.1"), _lane("epic.2")),
        admission=_granted("L3", 10_000, 5_000),
        report=said.append,
    )

    detail = "\n".join(said)
    assert "10000 tokens forecast" in detail
    assert "sized: epic.1" in detail
    assert "assumed at the unsizeable-lane bound (measured): epic.2" in detail


def test_an_unsizeable_lane_is_bounded_rather_than_waved_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    _patch_readiness(monkeypatch, ranked=((1, "epic.1"),))
    _pass_fixture(
        monkeypatch,
        sizings={"epic.1": None},
        forecasts={},
        unsized_tokens=5_000,
    )
    said: list[str] = []

    supervise.dispatch_lanes(
        Path(),
        _session(_lane("epic.1")),
        admission=_granted("L3", 10_000, 9_999),
        report=said.append,
    )

    assert "assumed at the unsizeable-lane bound (measured): epic.1" in "\n".join(said)


def test_an_unsizeable_lane_still_dispatches_when_the_budget_covers_its_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

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


def test_a_stale_binding_with_nothing_unlanded_is_cleared(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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
    monkeypatch.setattr(
        loop, "_worktree_landed", lambda *_a: pytest.fail("a live lane is not inspected")
    )

    assert supervise.repair_stale_bindings(tmp_path, _session(_lane("epic.1", live=True))) == ()


def test_a_repair_counts_as_progress_so_the_pass_re_derives() -> None:

    assert supervise.should_continue((supervise.RoutedOutcome("epic.1", "repaired", "cleared"),))


def _seed_fixture(
    monkeypatch: pytest.MonkeyPatch,
    *,
    steps: tuple[loop.AdvanceResult, ...],
    derived: supervise.SessionState | None = None,
    stop: loop.CeremonyResult | None = None,
) -> list[str]:

    _patch_readiness(monkeypatch)
    advanced: list[str] = []
    after = derived if derived is not None else _cold_session()

    def fake_run_ceremony(
        _repo: Path, issue_id: str, *, grant_root: str | None = None, **kwargs: object
    ) -> loop.CeremonyResult:
        assert not kwargs.get("interactive"), "a supervised pass has no human at a TTY"
        assert not kwargs.get("confirms"), "seeding relays no one-time code"
        advanced.append(f"{issue_id}@{grant_root}")
        return (
            loop.CeremonyResult(steps) if stop is None else dataclasses.replace(stop, events=steps)
        )

    monkeypatch.setattr(supervise.loop, "run_ceremony", fake_run_ceremony)
    monkeypatch.setattr(supervise, "derive_session", lambda *_a, **_k: after)
    return advanced


def _step(action: str, *, to_phase: str = "build") -> loop.AdvanceResult:
    return loop.AdvanceResult("epic", "decompose", to_phase, action, f"{action} detail")


def _cold_session() -> supervise.SessionState:
    return supervise.SessionState(
        root_issue="epic", root_status="open", children=(("epic.1", "open"),), adopted=()
    )


def test_a_cold_root_provisions_its_lanes_instead_of_reporting_nothing_to_land(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    advanced = _seed_fixture(monkeypatch, steps=(_step("decomposed"),))

    routed = supervise.seed_lanes(tmp_path, _cold_session())

    assert advanced == ["epic@epic"], "the root is advanced, under its own session's grant"
    assert [(r.issue_id, r.route) for r in routed] == [("epic", "seeded")]
    assert supervise.should_continue(routed), "the seeded lanes must be dispatched next pass"


def test_a_labelled_pass_provisions_its_own_lanes_instead_of_advancing_the_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    advanced = _seed_fixture(
        monkeypatch,
        steps=(_step("decomposed"),),
        derived=_session(_lane("origin.1"), _lane("other.9")),
    )
    provisioned: list[tuple[str, tuple[tuple[str, str], ...]]] = []
    monkeypatch.setattr(
        supervise.loop,
        "ensure_lane_worktrees",
        lambda _r, root, lanes: (
            provisioned.append((root, tuple(lanes))) or tuple(issue_id for issue_id, _ in lanes)
        ),
    )
    cut = supervise.SessionState(
        root_issue="epic",
        root_status="open",
        children=(("origin.1", "open"), ("other.9", "open"), ("done.2", "closed")),
        adopted=(),
        lane_label="release-v0.7.0",
    )

    routed = supervise.seed_lanes(tmp_path, cut)

    assert advanced == [], "the root's own advance provisions its children, not the cut"
    assert provisioned == [("epic", (("origin.1", "open"), ("other.9", "open")))], (
        "a closed bead of the cut is not provisioned a worktree"
    )
    assert [(r.issue_id, r.route) for r in routed] == [("epic", "seeded")]
    assert supervise.should_continue(routed), "the pass must go on to dispatch what it built"
    assert "2 dispatchable" in routed[0].detail


def test_a_labelled_pass_that_provisioned_nothing_stops_rather_than_spinning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _seed_fixture(monkeypatch, steps=(_step("decomposed"),))
    monkeypatch.setattr(supervise.loop, "ensure_lane_worktrees", lambda _r, _root, _lanes: ())
    cut = supervise.SessionState(
        root_issue="epic",
        root_status="open",
        children=(("origin.1", "open"),),
        adopted=(),
        lane_label="release-v0.7.0",
    )

    routed = supervise.seed_lanes(tmp_path, cut)

    assert [r.route for r in routed] == ["seed-blocked"]
    assert not supervise.should_continue(routed)
    assert "1 lane(s) selected by label 'release-v0.7.0'" in routed[0].detail


def test_a_root_that_cannot_seed_stops_the_session_rather_than_spinning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    _seed_fixture(monkeypatch, steps=(_step("blocked", to_phase="decompose"),))

    routed = supervise.seed_lanes(tmp_path, _cold_session())

    assert [r.route for r in routed] == ["seed-blocked"]
    assert not supervise.should_continue(routed)
    assert "1 open child(ren)" in routed[0].detail


def test_a_blocked_root_that_provisioned_lanes_routes_seeded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    advanced = _seed_fixture(
        monkeypatch,
        steps=(_step("blocked", to_phase="decompose"),),
        derived=_session(_lane("epic.1"), _lane("epic.2")),
    )

    routed = supervise.seed_lanes(tmp_path, _cold_session())

    assert advanced == ["epic@epic"]
    assert [(r.issue_id, r.route) for r in routed] == [("epic", "seeded")]
    assert supervise.should_continue(routed), "the pass must go on to dispatch what it built"
    assert "provisioned 2 dispatchable lane(s)" in routed[0].detail


def test_provisioned_lanes_that_cannot_dispatch_say_so_instead_of_claiming_none_exist(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

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


def test_a_seeding_refusal_names_the_level_that_would_delegate_the_checkpoint(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    _seed_fixture(
        monkeypatch,
        steps=(_step("blocked", to_phase="decompose"),),
        stop=loop.CeremonyResult(challenge=("decompose", "A1B2C3")),
    )

    routed = supervise.seed_lanes(tmp_path, _cold_session())

    assert [r.route for r in routed] == ["seed-blocked"]
    assert "no grant covers the decompose checkpoint" in routed[0].detail
    assert "an L1 grant delegates it" in routed[0].detail
    assert "basicly policy grant epic --level L1" in routed[0].detail
    assert "A1B2C3" not in routed[0].detail, "a one-time code is not routing detail"


def test_a_grant_that_declined_the_checkpoint_says_so_rather_than_asking_for_a_human(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    _seed_fixture(
        monkeypatch,
        steps=(_step("blocked", to_phase="decompose"),),
        stop=loop.CeremonyResult(
            challenge=("decompose", "A1B2C3"),
            challenge_reason="the active L1 grant on epic does not cover epic.9",
        ),
    )

    routed = supervise.seed_lanes(tmp_path, _cold_session())

    assert [r.route for r in routed] == ["seed-blocked"]
    assert "the decompose checkpoint was not delegated: the active L1 grant" in routed[0].detail
    assert "no grant covers" not in routed[0].detail


def test_a_refused_checkpoint_is_reported_as_a_refusal_not_as_an_absent_lane(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _seed_fixture(
        monkeypatch,
        steps=(_step("blocked", to_phase="decompose"),),
        stop=loop.CeremonyResult(refused=("decompose", "invalid or expired confirm code")),
    )

    routed = supervise.seed_lanes(tmp_path, _cold_session())

    assert [r.route for r in routed] == ["seed-blocked"]
    assert "the decompose checkpoint refused: invalid or expired" in routed[0].detail


def test_a_seeded_pass_carries_no_authorization_footnote(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _seed_fixture(monkeypatch, steps=(_step("decomposed"),))

    routed = supervise.seed_lanes(tmp_path, _cold_session())

    assert [r.route for r in routed] == ["seeded"]
    assert "grant" not in routed[0].detail


def test_seeding_is_skipped_while_a_lane_is_already_dispatchable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_readiness(monkeypatch, ranked=((1, "epic.1"),))
    monkeypatch.setattr(
        supervise.loop,
        "run_ceremony",
        lambda *_a, **_k: pytest.fail("a pass with work to dispatch must not re-seed"),
    )

    assert supervise.seed_lanes(tmp_path, _session(_lane("epic.1"))) == ()


def test_seeding_is_skipped_when_every_child_of_the_root_is_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_readiness(monkeypatch)
    monkeypatch.setattr(
        supervise.loop,
        "run_ceremony",
        lambda *_a, **_k: pytest.fail("there is no child to fan out"),
    )

    exhausted = supervise.SessionState("epic", "open", children=(("epic.1", "closed"),), adopted=())
    assert supervise.seed_lanes(tmp_path, exhausted) == ()


def test_a_childless_root_seeds_itself_as_the_single_lane(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    advanced = _seed_fixture(monkeypatch, steps=(_step("dispatched"),))

    leaf = supervise.SessionState("leaf", "open", children=(), adopted=())
    routed = supervise.seed_lanes(tmp_path, leaf)

    assert advanced == ["leaf@leaf"], "the leaf root is advanced under its own grant"
    assert [(r.issue_id, r.route) for r in routed] == [("leaf", "seeded")]


def test_a_childless_root_that_is_not_dispatchable_does_not_seed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_readiness(monkeypatch)
    monkeypatch.setattr(
        supervise.loop,
        "run_ceremony",
        lambda *_a, **_k: pytest.fail("a non-dispatchable leaf must not be advanced"),
    )

    parked = supervise.SessionState("leaf", "deferred", children=(), adopted=())
    assert supervise.seed_lanes(tmp_path, parked) == ()


_HEADLESS_SPEC = runner.RunnerSpec("claude", runner.HEADLESS, command=("claude", "-p", "{prompt}"))


def _ungranted() -> policy.SpendStatus:
    return policy.SpendStatus(grant=None, spent_tokens=0, halted=False)


def test_a_metered_dispatch_runs_and_says_no_budget_covers_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    _patch_readiness(monkeypatch, ranked=((1, "epic.1"),))
    monkeypatch.setattr(supervise.runner, "select_runner", lambda *_a, **_k: _HEADLESS_SPEC)
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

    assert outcomes, "an ungranted metered pass was still refused"
    assert queued == [], "and it still queued a refusal nobody has to answer"
    assert any("metered and no budget covers it" in line for line in lines)


def test_a_handoff_dispatch_needs_no_budget_because_it_spends_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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


def test_the_dispatch_note_names_the_tier_and_the_resolved_model() -> None:
    outcome = supervise.LaneOutcome(
        issue_id="epic.1",
        runner_name="claude",
        result=None,
        needs_fact=None,
        occupancy=None,
        overrun=False,
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
    outcome = supervise.LaneOutcome(
        issue_id="epic.1",
        runner_name="claude",
        result=None,
        needs_fact=None,
        occupancy=None,
        overrun=False,
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
    outcome = supervise.LaneOutcome(
        issue_id="epic.1",
        runner_name="manual",
        result=None,
        needs_fact=None,
        occupancy=None,
        overrun=False,
        detail="handed off",
    )

    assert outcome.model_note == ""


def test_a_running_lane_reports_its_elapsed_time_while_it_runs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

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


def _turn(tokens: int) -> runner.StreamEvent:
    return runner.StreamEvent(
        line=json.dumps({"type": "assistant"}),
        data={"type": "assistant"},
        usage=runner.Usage(tokens=tokens, cost=None, estimated=False),
    )


def test_lane_stream_moves_its_fingerprint_and_accrues_on_every_event() -> None:
    stream = supervise.LaneStream()
    idle = stream.fingerprint()

    stream(_turn(120))
    first = stream.fingerprint()
    stream(_turn(120))

    assert first != idle
    assert stream.fingerprint() != first
    assert (stream.events, stream.spent) == (2, 240)


def test_lane_stream_counts_an_event_that_carries_no_usage() -> None:
    stream = supervise.LaneStream()

    stream(runner.StreamEvent(line="Warning: starting up"))

    assert stream.events == 1
    assert stream.spent == 0


def test_lane_stream_keeps_the_last_thing_the_dispatch_said() -> None:
    stream = supervise.LaneStream()

    stream(runner.StreamEvent(line="x", text="Reading the plan gate\nthen the tests"))
    stream(runner.StreamEvent(line="y", text="Running the suite"))

    assert stream.doing == "Running the suite"


def test_lane_stream_names_the_nested_agent_that_said_it() -> None:
    stream = supervise.LaneStream()

    stream(runner.StreamEvent(line="x", text="Auditing the seam", subagent="reviewer"))

    assert stream.doing == "reviewer: Auditing the seam"


def test_lane_stream_reports_nothing_for_a_dispatch_that_has_said_nothing() -> None:
    stream = supervise.LaneStream()

    stream(_turn(120))
    stream(runner.StreamEvent(line="y", text="   \n  "))

    assert stream.doing == ""
    with supervise.live_lane("epic.1", stream):
        assert supervise.inflight_activity() == {}


def test_lane_stream_clips_a_long_turn_to_one_heartbeat_row() -> None:
    stream = supervise.LaneStream()

    stream(runner.StreamEvent(line="x", text="w" * 500))

    assert len(stream.doing) == supervise._SAID_CHARS + len("...")
    assert stream.doing.endswith("...")


def test_dispatch_lane_drives_the_watchdog_and_the_spend_meter_from_the_stream(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    codex = _codex()
    _worker_fixture(monkeypatch, tmp_path, stdout=_codex_events(50_000))
    monkeypatch.setattr(supervise, "lane_activity", lambda _cwd: "frozen")
    probe = _capture_stall_probe(monkeypatch)
    seen: dict[str, object] = {}

    def fake_run(
        spec: runner.RunnerSpec, *_a: object, on_event: runner.EventSink, **_kw: object
    ) -> runner.RunResult:
        seen["quiet"] = probe()
        seen["before"] = supervise.inflight_spend()
        on_event(_turn(31_337))
        seen["busy"] = probe()
        seen["after"] = supervise.inflight_spend()
        return runner.RunResult(
            spec.name, (spec.name,), executed=True, returncode=0, stdout=_codex_events(50_000)
        )

    monkeypatch.setattr(supervise.runner, "run", fake_run)

    supervise._dispatch_lane(tmp_path, _session(_lane("epic.1")), _lane("epic.1"), codex, _sizing())

    assert seen["busy"] != seen["quiet"]
    assert seen["before"] == {"epic.1": 0}
    assert seen["after"] == {"epic.1": 31_337}
    assert supervise.inflight_spend() == {}


def test_dispatch_lane_keeps_the_git_probe_beside_the_stream(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    codex = _codex()
    _worker_fixture(monkeypatch, tmp_path, stdout=_codex_events(50_000))
    activity = {"reading": "before"}
    monkeypatch.setattr(supervise, "lane_activity", lambda _cwd: activity["reading"])
    probe = _capture_stall_probe(monkeypatch)
    seen: dict[str, object] = {}

    def fake_run(spec: runner.RunnerSpec, *_a: object, **_kw: object) -> runner.RunResult:
        seen["before"] = probe()
        activity["reading"] = "after the commit"
        seen["after"] = probe()
        return runner.RunResult(spec.name, (spec.name,), executed=True, returncode=0, stdout="")

    monkeypatch.setattr(supervise.runner, "run", fake_run)

    supervise._dispatch_lane(tmp_path, _session(_lane("epic.1")), _lane("epic.1"), codex, _sizing())

    assert seen["before"] != seen["after"]


def _capture_stall_probe(monkeypatch: pytest.MonkeyPatch) -> Callable[[], str]:

    captured: dict[str, Callable[[], object]] = {}

    class _Watchdog:
        def __init__(
            self, _after: float, *, probe: Callable[[], str], on_stall: Callable[[], object]
        ) -> None:
            captured["probe"] = probe
            captured["on_stall"] = on_stall

        def __enter__(self) -> _Watchdog:
            return self

        def __exit__(self, *_exc: object) -> None:
            return None

    monkeypatch.setattr(supervise.runner, "StallWatchdog", _Watchdog)
    return lambda: str(captured["probe"]())


def test_inflight_note_reports_the_tokens_a_running_lane_has_reported() -> None:

    metered, unmetered = _lane("epic.1"), _lane("epic.2")
    futures: dict[Future[supervise.LaneOutcome], supervise.AdoptedLane] = {
        Future(): metered,
        Future(): unmetered,
    }
    started = {"epic.1": time.monotonic() - 12.0, "epic.2": time.monotonic() - 3.0}
    stream = supervise.LaneStream()
    stream(_turn(84_000))

    with (
        supervise.live_lane("epic.1", stream),
        supervise.live_lane("epic.2", supervise.LaneStream()),
    ):
        note = supervise._inflight_note(started, futures, set(futures))

    assert "epic.1 12s 84000 tok" in note
    assert "epic.2 3s," in note or note.endswith("epic.2 3s")


def test_inflight_note_says_what_a_running_lane_is_doing() -> None:
    lane = _lane("epic.1")
    futures: dict[Future[supervise.LaneOutcome], supervise.AdoptedLane] = {Future(): lane}
    started = {"epic.1": time.monotonic() - 12.0}
    stream = supervise.LaneStream()
    stream(runner.StreamEvent(line="x", text="Rebasing onto main", subagent="kai"))

    with supervise.live_lane("epic.1", stream):
        note = supervise._inflight_note(started, futures, set(futures))

    assert "[kai: Rebasing onto main]" in note


def test_the_over_report_bound_sits_above_every_measured_ratio() -> None:

    measured = {
        "basicly-vkh0.9": 7_426_083 / 4_160_032,
        "basicly-lpsf": 25_595_734 / 16_495_867,
        "basicly-vkh0.12": 11_994_844 / 7_730_640,
        "basicly-vkh0.11": 16_671_836 / 11_431_736,
    }
    assert max(measured.values()) < supervise.LIVE_OVERREPORT_BOUND
    assert min(measured.values()) > 1.0, "every sample must show live over recorded"


def test_a_lane_ceiling_reaches_the_dispatch_from_the_runner_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    codex = _codex()
    _worker_fixture(monkeypatch, tmp_path, stdout=_codex_events(50_000))
    seen: dict[str, object] = {}

    def fake_run(spec: runner.RunnerSpec, *_a: object, **kw: object) -> runner.RunResult:
        seen.update(kw)
        return runner.RunResult(spec.name, (spec.name,), executed=True, returncode=0, stdout="")

    monkeypatch.setattr(supervise.runner, "run", fake_run)
    base = supervise.load_runner_config(tmp_path)
    monkeypatch.setattr(
        supervise,
        "load_runner_config",
        lambda _r: dataclasses.replace(base, lane_token_ceiling=25_000),
    )

    supervise._dispatch_lane(tmp_path, _session(_lane("epic.1")), _lane("epic.1"), codex, _sizing())

    bounds = seen["bounds"]
    assert isinstance(bounds, runner.DispatchBounds)
    assert bounds.token_ceiling == 25_000, "the runner setting never reached the dispatch"


def test_dispatch_lane_bounds_the_run_on_its_quiet_stream_and_not_on_spend(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    codex = _codex()
    _worker_fixture(monkeypatch, tmp_path, stdout=_codex_events(50_000))
    seen: dict[str, object] = {}

    def fake_run(spec: runner.RunnerSpec, *_a: object, **kw: object) -> runner.RunResult:
        seen.update(kw)
        return runner.RunResult(spec.name, (spec.name,), executed=True, returncode=0, stdout="")

    monkeypatch.setattr(supervise.runner, "run", fake_run)

    supervise._dispatch_lane(
        tmp_path,
        _session(_lane("epic.1")),
        _lane("epic.1"),
        codex,
        _sizing(),
    )

    bounds = seen["bounds"]
    assert isinstance(bounds, runner.DispatchBounds)
    assert bounds.quiet_after == runner.DEFAULT_QUIET_AFTER
    assert bounds.token_ceiling is None, (
        "a lane ceiling is a runner setting; this pass declares none"
    )
    assert seen["timeout"] == supervise.load_runner_config(tmp_path).runner_timeout


def test_a_lane_stopped_on_a_bound_records_which_bound_and_names_it_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    codex = _codex()
    _worker_fixture(monkeypatch, tmp_path, stdout="")
    stopped = runner.StopReason(runner.QUIET_BOUND, "no stream events for 900s")

    monkeypatch.setattr(
        supervise.runner,
        "run",
        lambda spec, *_a, **_k: runner.RunResult(
            spec.name, (spec.name,), executed=True, timed_out=True, stopped=stopped
        ),
    )
    salvaged: list[str] = []
    monkeypatch.setattr(
        supervise.commit,
        "salvage",
        lambda _c, _b, *, reason: (
            salvaged.append(reason),
            supervise.commit.Salvage("committed", "the worktree was committed as abc1234"),
        )[1],
    )
    queued: list[tuple[str, str]] = []
    monkeypatch.setattr(
        supervise.decisions,
        "enqueue",
        lambda _r, issue, kind, question, *_a, **_k: (
            queued.append((kind, question)),
            decisions_item(issue, kind),
        )[1],
    )
    recorded: list[dict] = []
    monkeypatch.setattr(supervise.loop, "record_run", lambda *_a, **kw: recorded.append(kw))

    outcome = supervise._dispatch_lane(
        tmp_path, _session(_lane("epic.1")), _lane("epic.1"), codex, _sizing()
    )

    label = "quiet bound: no stream events for 900s"
    assert salvaged == [label]
    assert queued == [("stall", f"runner codex stopped on {label}: retry, re-dispatch, or park?")]
    assert label in outcome.detail
    assert recorded[0]["stopped_bound"] == runner.QUIET_BOUND


def test_a_wall_clock_kill_records_no_bound_because_the_outcome_already_says_so(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    codex = _codex()
    _worker_fixture(monkeypatch, tmp_path, stdout="")
    monkeypatch.setattr(
        supervise.runner,
        "run",
        lambda spec, *_a, **_k: runner.RunResult(
            spec.name, (spec.name,), executed=True, timed_out=True
        ),
    )
    monkeypatch.setattr(
        supervise.decisions,
        "enqueue",
        lambda _r, issue, kind, *_a, **_k: decisions_item(issue, kind),
    )
    recorded: list[dict] = []
    monkeypatch.setattr(supervise.loop, "record_run", lambda *_a, **kw: recorded.append(kw))

    supervise._dispatch_lane(tmp_path, _session(_lane("epic.1")), _lane("epic.1"), codex, _sizing())

    assert recorded[0]["stopped_bound"] is None


def test_the_stall_flag_names_the_bound_a_quiet_lane_will_actually_reach(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    queued: list[str] = []
    monkeypatch.setattr(
        supervise.decisions,
        "enqueue",
        lambda _r, issue, kind, _q, detail="", **_k: (
            queued.append(detail),
            decisions_item(issue, kind),
        )[1],
    )

    supervise.flag_stalled_lane(tmp_path, "epic.1", 900.0, 1800.0)

    assert queued == [
        "no commits and no file changes for 900s; the run continues "
        "until the quiet bound (1800s), still holding a lane slot"
    ]


_CLAUDE = next(spec for spec in runner.BUILTIN_RUNNERS if spec.name == "claude")


def test_a_lane_build_dispatch_inherits_the_features_seed(tmp_path: Path) -> None:
    runner.record_session_seed(tmp_path, "basicly-2kh170", "claude", "s-1")

    seed = supervise._lane_seed(tmp_path, "basicly-2kh170", _CLAUDE)

    assert seed == runner.SessionSeed("s-1", exists=True)


def test_only_a_lane_that_returned_clean_records_its_new_seed(tmp_path: Path) -> None:
    minted = runner.SessionSeed("s-new", exists=False)
    failed = runner.RunResult("claude", (), executed=True, returncode=1)

    supervise._keep_lane_seed(tmp_path, "basicly-2kh170", _CLAUDE, minted, failed)
    assert runner.session_seed(tmp_path, "basicly-2kh170", "claude").exists is False

    ok = runner.RunResult("claude", (), executed=True, returncode=0)
    supervise._keep_lane_seed(tmp_path, "basicly-2kh170", _CLAUDE, minted, ok)
    assert runner.session_seed(tmp_path, "basicly-2kh170", "claude") == runner.SessionSeed(
        "s-new", exists=True
    )


def test_a_family_that_cannot_fork_is_never_handed_a_seed(tmp_path: Path) -> None:
    codex = next(spec for spec in runner.BUILTIN_RUNNERS if spec.name == "codex")

    assert supervise._lane_seed(tmp_path, "basicly-2kh170", codex) is None


def _refused_tracker_sync(issue_id: str):

    def advance(_r: Path, lane: str, **_k: object) -> loop.AdvanceResult:
        if lane == issue_id:
            raise supervise.merge.TrackerCommitRefusedError(
                "`pre-commit-script` refused: FAILED: release-notes (0.06s) "
                "· checks failed: 32/33 passed"
            )
        return _advance_result(lane, "merged", "verify", "landed")

    return advance


def test_a_refused_tracker_sync_leaves_the_lane_ready_to_land(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    monkeypatch.setattr(supervise.loop, "advance", _refused_tracker_sync("epic.1"))

    routed = supervise.route_outcomes(
        tmp_path, _session(_lane("epic.1")), (_executed_outcome("epic.1"),)
    )

    assert [r.route for r in routed] == [supervise.READY_TO_LAND]
    assert "FAILED: release-notes" in routed[0].detail
    assert supervise.carried_forward(routed) == frozenset({"epic.1"})
    assert supervise.should_continue(routed) is True


def test_a_refused_tracker_sync_does_not_hold_the_later_lanes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(supervise.loop, "advance", _refused_tracker_sync("epic.1"))
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
    monkeypatch.setattr(supervise.merge, "landing_order", lambda _r, items: items)

    routed = supervise.route_outcomes(
        tmp_path,
        _session(_lane("epic.1"), _lane("epic.2")),
        (_executed_outcome("epic.1"), _executed_outcome("epic.2")),
    )

    assert [r.route for r in routed] == [supervise.READY_TO_LAND, "merged"]
