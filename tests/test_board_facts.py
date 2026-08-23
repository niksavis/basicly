"""Tests for the caller-side derivations the board producer refuses to make.

Every case here asserts the same property from a different side: an unfillable fact is an
**absence**, never a zero. That is the rule `board_sections` documents and the reason this
module exists, so it is what the tests bind rather than any particular value.

Driven against a `tmp_path` repository, never this one: a gate asserted on the live tracker
becomes a report on whatever the tracker holds today, and any lane editing a `## Plan` turns
the suite red.
"""

from __future__ import annotations

import json
import shutil
import subprocess  # nosec B404
from pathlib import Path

import pytest

from basicly import board_facts, board_sections, integrity, loop_state, supervise, tracker
from basicly.config import VERIFY_GATE_PROVIDER

REPO_ROOT = Path(__file__).parent.parent
KIT_SOURCE = REPO_ROOT / ".basicly" / "core" / "kit" / "tracker"


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)  # nosec B603 B607


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """A real git repo with one commit on ``probe``, so the branch name is not the box's.

    Driven against git rather than a stubbed one: `_repo_facts` is a reading of
    ``status --porcelain=v1 -b`` output, so a fake would assert this module's idea of git.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "probe")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.com")
    (repo / "README.md").write_text("hi\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "init")
    return repo


def _owned_repo(root: Path, *records: str) -> Path:
    """A checkout with the kit installed and *records* open in its own ledger.

    Seeded through the kit for the reason `test_tracker_seam._owned_repo` gives, and hermetic
    for the reason `test_board_snapshot` gives: every count below is this corpus's, so it
    cannot go red on the next landing the way an assertion against the live log would.
    """
    (root / tracker.KIT_TRACKER_DIR).mkdir(parents=True, exist_ok=True)
    for source in sorted(KIT_SOURCE.glob("*.py")):
        shutil.copy2(source, root / tracker.KIT_TRACKER_DIR / source.name)
    (root / tracker.LEDGER_DIR).mkdir(parents=True, exist_ok=True)
    (root / "basicly.toml").write_text('[tracker]\nmode = "owned"\n', encoding="utf-8")
    kit = tracker.kit(root)
    kit.events.append(
        tracker.ledger_dir(root),
        [
            kit.events.Draft(record, kit.events.KIND_STATUS, {"status": "open"})
            for record in records
        ],
    )
    return root


def _seeded(repo: Path, *, level: str) -> Path:
    """``bd-1`` bound to a worktree with a green verify gate, at *level* where one is given.

    Written through the engine's own writers rather than hand-spelled markers: the phase under
    test turns on the ``[harness-classification]`` body and on the gate provider a required
    gate counts, and an invented spelling of either would agree with itself on both paths.
    """
    if level:
        tracker.add_comment(repo, "bd-1", f"{integrity.CLASSIFICATION_MARKER} level={level}")
    tracker.write(
        repo,
        ["update", "bd-1", "--external-ref", loop_state.format_worktree_ref("w", "harness/w")],
    )
    tracker.write(
        repo,
        # The argv `validate_gate.record_verdict` writes, on the gate the loop's landing records.
        [
            "gate",
            "report",
            "bd-1",
            "--gate",
            "verify",
            "--provider",
            VERIFY_GATE_PROVIDER,
            "--status",
            "pass",
        ],
    )
    return repo


def test_no_supervisor_lock_yields_no_session_facts(tmp_path: Path) -> None:
    """A guessed root would be a claim about which pass is running, drawn on a wall."""
    assert board_facts.session_facts(tmp_path) is None


def test_no_grant_yields_no_grant(tmp_path: Path) -> None:
    """An absent grant is an absent window, not a zero ceiling."""
    assert board_facts.active_grant(tmp_path, "demo-1") is None


def test_no_grant_yields_no_spend(tmp_path: Path) -> None:
    """Spend under no grant is unknown. A zero here reads as "nothing was spent"."""
    assert board_facts.grant_spend(tmp_path, "demo-1", None) is None


def test_an_unreadable_tracker_yields_no_readiness(tmp_path: Path) -> None:
    """`ready` absent beats `ready: 0`: one says unknown, the other says nothing is ready."""
    assert board_facts.readiness(tmp_path) is None


def test_an_unreadable_tracker_yields_no_phases(tmp_path: Path) -> None:
    """An empty map omits the key per unit rather than inventing a phase for any of them."""
    assert board_facts.phases(tmp_path) == {}


def test_a_document_with_no_asks_yields_no_questions(tmp_path: Path) -> None:
    """Nothing to pair against is not a wait with an empty question."""
    assert board_facts.questions(tmp_path, {}) == {}


def test_the_session_section_is_omitted_rather_than_guessed(tmp_path: Path) -> None:
    """A root invented here would be a claim about which pass is running, drawn on a wall."""
    assert board_facts.session_facts(tmp_path) is None


def test_a_held_lock_supplies_the_session_facts_the_producer_may_not_derive(tmp_path: Path) -> None:
    """The C11 inversion, exercised: this layer reads the lock and passes the facts down.

    The producer may not read it itself - the import would close
    `supervise -> board_snapshot -> supervise`, since the supervisor emits a snapshot too.
    """
    lock = tmp_path / supervise.LOCK_FILE
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(json.dumps({"pid": 1, "session_id": "abc", "root_issue": "x-1"}))
    facts = board_facts.session_facts(tmp_path)
    assert facts is not None
    assert facts.root_issue == "x-1"
    assert facts.session_id == "abc"
    assert facts.supervised is True
    assert facts.stale is False


def test_the_git_state_the_producer_may_not_read_comes_from_this_layer(git_repo: Path) -> None:
    """basicly-f3tked: `dirty` is a subprocess, so the layer that may spawn one supplies it.

    The clean reading first, because it is the one an implementation can get wrong in the
    dangerous direction: a parser that counts its own header line as a change reports every
    tree dirty, and a wall board that always says dirty says nothing.
    """
    clean = board_facts.repo_facts(git_repo)
    assert clean is not None
    assert clean.branch == "probe"
    assert clean.dirty is False
    assert len(clean.head) >= 7

    (git_repo / "README.md").write_text("changed\n", encoding="utf-8")
    dirty = board_facts.repo_facts(git_repo)
    assert dirty is not None
    assert dirty.dirty is True
    assert dirty.head == clean.head


def test_a_directory_git_will_not_answer_for_keeps_the_repo_section_to_its_name(
    tmp_path: Path,
) -> None:
    """The negative control on the reading above: no repo, no branch, and no exception."""
    assert board_facts.repo_facts(tmp_path) is None


def test_the_phase_map_covers_every_record_and_folds_the_log_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """basicly-s1vqq2: the map is the whole population now, and the cost that capped it is gone.

    **A spy, not a stopwatch**, the instrument `test_board_snapshot` already uses on the
    producer beside this one: the defect was seven whole-log reads per record - 591 ms over 20,
    so 138 s for 234 - and the property that fixes it is the fold count. A duration assertion
    would instead fail on whichever runner is slowest that day.

    The subprocess refusal rides along because a fold reached through a tracker spawn would
    satisfy the count while paying the cost this removes.
    """
    records = tuple(f"bd-{index}" for index in range(11))
    repo = _owned_repo(tmp_path, *records)
    kit = tracker.kit(repo)
    folds: list[int] = []
    real_fold = kit.events.fold

    def counting_fold(*args: object, **kwargs: object) -> object:
        folds.append(1)
        return real_fold(*args, **kwargs)

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the phase map spawned a subprocess")

    monkeypatch.setattr(kit.events, "fold", counting_fold)
    monkeypatch.setattr(subprocess, "Popen", refuse)

    phases = board_facts.phases(repo)

    assert len(folds) == 1
    assert set(phases) == set(records)
    assert all(value for value in phases.values())


def test_a_mapped_phase_is_the_engine_derivation_on_a_unit_owing_validation(
    tmp_path: Path,
) -> None:
    """The map calls `loop_state.derive_phase`, and this is the case that proves which one.

    An L3 unit whose verify gate is green and whose validate gate is not is in ``validate``,
    and only a reader that knows the recorded level can say so - the kit ships a phase
    derivation folded out of the ledger alone, which reads ``verify`` here and renders
    identically. The L2 repo beside it is the control: same gate, same binding, no level
    marker, and the answer has to differ or the marker is not being read at all.

    Both are also checked against `read_node_state`, the per-record route this replaces -
    measured on this repository's own log, 236 active records agreed on both paths, 128.1 s
    against 0.125 s.
    """
    l3 = _seeded(_owned_repo(tmp_path / "l3", "bd-1"), level="L3")
    l2 = _seeded(_owned_repo(tmp_path / "l2", "bd-1"), level="")

    assert loop_state.phase_map(l3)["bd-1"] == "validate"
    assert loop_state.phase_map(l2)["bd-1"] == "verify"
    for repo in (l3, l2):
        assert loop_state.phase_map(repo)["bd-1"] == loop_state.read_node_state(repo, "bd-1").phase


def _view(issue_id: str, *, live: bool) -> supervise.LaneView:
    """A lane binding as the tracker holds it, with no run history of its own."""
    return supervise.LaneView(
        issue_id=issue_id,
        status="open",
        worktree=issue_id,
        branch=f"harness/{issue_id}",
        live=live,
        last_agent="claude",
        last_tokens=11,
    )


_RUN = {
    "agent": "claude",
    "model": "claude-opus-5",
    "cost": 12.5,
    "duration_s": 900.0,
    "context_tokens": 180_000,
    "context_window": 1_000_000,
}


def test_a_running_lane_carries_what_it_is_spending_and_saying_now() -> None:
    """The live stream's figures reach the card, and they beat the last run's."""
    fact = board_facts._lane_fact(
        _view("a", live=True), {"a": "build"}, {"a": 5_000_000}, {"a": "reading the gate"}, [_RUN]
    )
    assert fact.tokens == 5_000_000
    assert fact.note == "reading the gate"
    assert fact.model == "claude-opus-5"


def test_a_running_lane_does_not_inherit_the_last_dispatch_cost_or_occupancy() -> None:
    """Per-dispatch figures are omitted while a lane runs, rather than carried forward.

    The failure this refuses is quiet: last run's cost printed under a heading that says the
    lane is running now reads as this run's, and nothing on the card would say otherwise.
    `spending` carries the key with `0`: registered and not yet metered is still running.
    """
    fact = board_facts._lane_fact(_view("a", live=True), {"a": "build"}, {"a": 0}, {}, [_RUN])
    assert fact.live is True
    assert fact.cost_usd is None
    assert fact.elapsed_s is None
    assert fact.context_used is None
    assert fact.context_window is None


def test_a_provisioned_lane_with_no_live_stream_is_not_reported_as_running() -> None:
    """basicly-ze0po3: a worktree on disk outlives the agent that made it (rn0o.6 v. fi1i7z).

    `view.live` only says the tracker's worktree binding still exists, which a schema
    consumer reads as "still running" and stays true for hours after the process that made
    it has exited. `spending` names exactly the lanes with a live stream registered right
    now, so a lane absent from it is idle rather than running, and its last known figures
    speak instead of a blank "running" card carrying no note and no tokens. The worktree
    fact itself does not disappear - it travels as `provisioned`.
    """
    fact = board_facts._lane_fact(_view("a", live=True), {"a": "build"}, {}, {}, [_RUN])
    assert fact.live is False
    assert fact.provisioned is True
    assert fact.tokens == 11
    assert (fact.cost_usd, fact.elapsed_s) == (12.5, 900.0)
    row = board_sections.lanes([fact])[0]
    assert row["live"] is False
    assert row["provisioned"] is True


def test_a_finished_lane_carries_every_figure_its_run_record_holds() -> None:
    """The control for the case above: the same record, read off a lane that is not live."""
    fact = board_facts._lane_fact(_view("a", live=False), {"a": "build"}, {}, {}, [_RUN])
    assert (fact.cost_usd, fact.elapsed_s) == (12.5, 900.0)
    assert (fact.context_used, fact.context_window) == (180_000, 1_000_000)


def test_a_lane_with_no_run_record_states_no_figure_it_was_not_given() -> None:
    """An unfillable fact stays absent, which is this module's whole rule."""
    fact = board_facts._lane_fact(_view("a", live=False), {"a": "build"}, {}, {}, [])
    assert fact.model == ""
    assert fact.note == ""
    assert (fact.cost_usd, fact.context_used) == (None, None)


def test_a_boolean_is_not_read_as_a_measurement() -> None:
    """`True` is an `int` in Python, so a truthy field would otherwise price a lane at 1."""
    fact = board_facts._lane_fact(
        _view("a", live=False), {"a": "build"}, {}, {}, [{"cost": True, "context_tokens": False}]
    )
    assert fact.cost_usd is None
    assert fact.context_used is None


def test_a_lane_that_has_reported_zero_tokens_states_no_spend_at_all() -> None:
    """A live meter registered but not yet reporting omits `tokens` rather than stating 0.

    The stream is published the instant a dispatch starts, so `inflight_spend` carries a real
    `0` for every lane between its registration and its first metered turn - the exact window
    the defect was reported in. `0 tok` on a card reads as a measured figure and a free lane.
    """
    view = supervise.LaneView(
        issue_id="a", status="open", worktree="a", branch="harness/a", live=True
    )
    fact = board_facts._lane_fact(view, {"a": "build"}, {"a": 0}, {}, [])
    assert fact.tokens is None
    assert "tokens" not in board_sections.lanes([fact])[0]


def test_the_zero_window_does_not_fall_through_to_a_previous_run() -> None:
    """The zero window with something to fall back to, which is where it actually bit.

    `test_a_lane_that_has_reported_zero_tokens_states_no_spend_at_all` pins the same window
    on a lane with no run history, where a falsy test resolves to `None` by luck rather than
    by rule. Give the lane a previous dispatch and the two stop agreeing: a falsy test hands
    the window that dispatch's total, and the card reads as a lane that spent ten million
    tokens in its first second.
    """
    fact = board_facts._lane_fact(_view("a", live=True), {"a": "build"}, {"a": 0}, {}, [])
    assert _view("a", live=True).last_tokens == 11
    assert fact.tokens is None


def test_a_provisioned_lane_falls_back_exactly_like_a_finished_one() -> None:
    """basicly-ze0po3: the worktree still existing must not change what a card falls back to.

    That distinction is `provisioned` now, and `live` answers only whether the supervisor
    has a stream registered for the lane.
    """
    provisioned = board_facts._lane_fact(_view("a", live=True), {"a": "build"}, {}, {}, [])
    finished = board_facts._lane_fact(_view("a", live=False), {"a": "build"}, {}, {}, [])
    assert (provisioned.live, provisioned.provisioned) == (False, True)
    assert (finished.live, finished.provisioned) == (False, False)
    assert provisioned.tokens == finished.tokens == 11


def test_a_finished_lane_does_fall_back_to_its_last_recorded_run() -> None:
    """The control: the fallback is not removed, it is confined to lanes that are not live."""
    fact = board_facts._lane_fact(_view("a", live=False), {"a": "build"}, {}, {}, [])
    assert fact.tokens == 11
