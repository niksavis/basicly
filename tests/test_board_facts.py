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

from basicly import (
    board_facts,
    board_snapshot,
    integrity,
    loop_state,
    supervise,
    tracker,
)
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


def test_live_grant_spend_is_absent_then_advances_and_rides_named_apart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """basicly-wctp0g: no ceiling/stream is None; once live it advances, named apart, bounded."""
    assert board_facts.live_grant_spend(board_snapshot.SessionFacts(root_issue="x-1")) is None
    session = board_snapshot.SessionFacts(root_issue="x-1", token_budget=1_000_000, spent_tokens=10)
    assert board_facts.live_grant_spend(session) is None
    built: dict[str, object] = {"session": {"spent_tokens": 10}}
    monkeypatch.setattr(supervise, "inflight_spend", lambda: {"x-1.1": 200})
    first = board_facts.live_grant_spend(session)
    monkeypatch.setattr(supervise, "inflight_spend", lambda: {"x-1.1": 500})
    second = board_facts.live_grant_spend(session)
    assert (first, second) == (210, 510)
    section = board_facts._with_live_spend(built, session)["session"]
    assert isinstance(section, dict)
    assert (section["spent_tokens"], section["spent_tokens_live"]) == (10, 510)
    assert section["spent_tokens_live_over_estimate"] is True
    assert section["spent_tokens_live_bound"] == supervise.LIVE_OVERREPORT_BOUND


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


def _ask(kind: str, issue: str, subject: str = "") -> dict[str, str]:
    row = {"wait_id": f"{issue}#wait-{subject or 'x'}", "issue": issue, "kind": kind}
    return {**row, "subject": subject} if subject else row


def test_visible_asks_matches_each_acceptance_case() -> None:
    """basicly-0i86tl AC1-4: a delegated or closed checkpoint drops; a decision never does."""
    filt, both = board_facts._visible_asks, frozenset({"bd-1", "bd-2"})
    classify, decision = _ask("checkpoint", "bd-1", "classify"), _ask("decision", "bd-2")
    assert [a["issue"] for a in filt([classify, decision], both, "L3")] == ["bd-2"]  # AC1
    assert filt([classify], frozenset({"bd-2"}), "") == []  # AC2
    assert filt([classify], both, "L1") == [classify]  # AC3
    assert filt([decision], both, "L3") == [decision]  # AC4


def test_hide_unanswerable_reads_the_grant_and_the_units_off_the_document() -> None:
    """The wiring `document` relies on: both facts come off the built document itself."""
    built = {
        "asks": [_ask("checkpoint", "bd-1", "classify"), _ask("decision", "bd-2")],
        "units": [{"id": "bd-1"}, {"id": "bd-2"}],
        "session": {"grant_level": "L3"},
    }
    board_facts._hide_unanswerable(built)
    assert [a["issue"] for a in built["asks"]] == ["bd-2"]
    assert board_facts._hide_unanswerable({"units": []}) == {"units": []}
