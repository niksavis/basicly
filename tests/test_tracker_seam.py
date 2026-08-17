"""The write seam and the flip's absence contract (basicly-vkh0.19, basicly-s5li).

What this file asserts, now that the ledger is the only store:

- **The flip is confined to the seam, and `read_record`'s one absence contract survives
  it.** :func:`test_no_module_outside_the_seam_reads_the_owned_store` is the tree guard
  for the "confined" half.
- **Every harness marker family is carried with no process spawned.** Driven through the
  engine's own recorders and read back off the ledger, not by asserting that a mirror
  function was called — and every test runs with a spawn wired to fail it, because "the
  binary was absent and the write silently went nowhere" would satisfy a weaker
  assertion and is exactly the failure mode this seam could have.

The dual write's own tests left with the store they compared against: the differential,
the live reference and the export-shrink guard all existed to keep two stores in step.
Nothing here spawns a process, sleeps, or reads the host's tracker.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from basicly import policy, run_record, tracker
from basicly.config import PolicyConfig

REPO_ROOT = Path(__file__).resolve().parent.parent
KIT_SOURCE = REPO_ROOT / ".basicly" / "core" / "kit" / "tracker"

# The engine's own gate provider, spelled as the kit's vocabulary expects to read it
# back. A foreign provider on a required gate is disregarded, so a test that used one
# would derive `missing` on a record it had just recorded a pass for.
ENGINE_PROVIDER = "basicly-verify"
FOREIGN_PROVIDER = "some-ci"


# --- fixtures -----------------------------------------------------------------


def _repo(tmp_path: Path, mode: str = tracker.MODE_OWNED) -> Path:
    """A checkout with the kit installed and ``[tracker] mode`` declared."""
    (tmp_path / tracker.KIT_TRACKER_DIR).mkdir(parents=True, exist_ok=True)
    for source in sorted(KIT_SOURCE.glob("*.py")):
        shutil.copy2(source, tmp_path / tracker.KIT_TRACKER_DIR / source.name)
    (tmp_path / tracker.LEDGER_DIR).mkdir(parents=True, exist_ok=True)
    (tmp_path / "basicly.toml").write_text(f'[tracker]\nmode = "{mode}"\n', encoding="utf-8")
    return tmp_path


@pytest.fixture(autouse=True)
def no_spawn(monkeypatch: pytest.MonkeyPatch) -> None:
    """A spawn fails the test rather than falling back to a store that no longer exists."""

    def refuse(cmd: list[str], **_kwargs: object) -> None:
        pytest.fail(f"the engine spawned a process after the flip: {cmd}")

    monkeypatch.setattr(subprocess, "run", refuse)


def _ledger_events(repo: Path) -> list[Any]:
    kit = tracker.kit(repo)
    return kit.read_ledger(tracker.ledger_dir(repo))


def _kinds(repo: Path, record: str) -> list[str]:
    return [event.kind for event in _ledger_events(repo) if event.record == record]


# --- the flip, and the absence contract it must not change --------------------


def test_a_bead_no_store_holds_reads_as_none(tmp_path: Path) -> None:
    """One absence contract, asserted against both stores.

    This is the criterion in its comparative form: the flip is only transparent if the
    owned store answers absence the same way the external binary does. The empty list is
    the natural in-process answer and it is exactly the case that split six call sites
    from five before `basicly-tcmy.14` made the choice once.
    """
    repo = _repo(tmp_path)
    tracker.create_record(repo, ["create", "a bead", "-t", "task", "--parent", "seam-0", "--json"])

    assert tracker.read_record(repo, "seam-9999") is None


def test_require_record_raises_one_message_naming_the_bead(tmp_path: Path) -> None:
    """The hard half of the contract: one message for every way a record is absent."""
    repo = _repo(tmp_path)
    with pytest.raises(RuntimeError, match="the tracker holds no usable record for seam-9999"):
        tracker.require_record(repo, "seam-9999")


def test_an_empty_ledger_reads_as_absence_not_as_a_failure(tmp_path: Path) -> None:
    """A repository whose ledger holds nothing is answered, not crashed.

    Indistinguishable from every record having been deleted, which is correct: the two
    are the same fact about what the tracker currently holds.
    """
    repo = _repo(tmp_path)
    assert tracker.read_record(repo, "seam-0001") is None


def test_a_tombstoned_record_reads_as_absent_after_the_flip(tmp_path: Path) -> None:
    """A deletion is an event, and the seam is where it becomes an absence.

    The log expresses a deletion by keeping the record and flagging it, which leaves the
    *status* untouched. A reader that served the tombstoned record would hand out work on
    a record somebody deleted — the defect `differential.is_ready` names, arriving one
    layer earlier.
    """
    repo = _repo(tmp_path)
    record = tracker.create_record(
        repo, ["create", "a bead", "-t", "task", "--parent", "s-1", "--json"]
    )
    kit = tracker.kit(repo)
    kit.events.append(
        tracker.ledger_dir(repo), [kit.events.Draft(record, kit.events.KIND_TOMBSTONE, {})]
    )

    assert tracker.read_record(repo, record) is None


def test_no_module_outside_the_seam_reads_the_owned_store() -> None:
    """The "confined to the seam" half of the criterion, checked against the tree.

    Eleven call sites used to unwrap ``br show --json`` by hand; `basicly-tcmy.14`
    collapsed them so that this bead would be an edit to one file. A second module
    reaching into the ledger — or branching on the mode — re-acquires the scatter, and
    the next cutover step pays for it again.
    """
    root = REPO_ROOT / "src" / "basicly"
    reaching = {
        "tracker.owned_record(",
        "tracker.tracker_mode(",
        "tracker.ledger_dir(",
        "tracker.kit(",
    }
    offenders = sorted(
        f"{path.name}: {name}"
        for path in sorted(root.glob("*.py"))
        if path.name != "tracker.py"
        for name in reaching
        if name in path.read_text(encoding="utf-8")
    )
    assert offenders == []


# --- step 5: the harness markers, carried without br (basicly-s5li) -----------
#
# The criterion is a *negative* about br plus a *positive* about the ledger, and both
# halves need saying: the engine records a checkpoint approval, a gate record, a grant,
# a rework counter and a dispatch record, reads every one of them back, and does it with
# br absent from PATH. So the fixture below does not merely un-install br — it makes a
# spawn fail the test, because "br was absent and the code silently degraded to writing
# nothing" would satisfy a weaker assertion and is exactly the failure mode this seam
# could have.


def _run(seed: str, *, tokens: int) -> run_record.RunRecord:
    """One dispatch record, keyed on *seed* so two dispatches can be told apart."""
    return run_record.RunRecord(
        agent="claude",
        outcome="EXECUTED",
        returncode=0,
        duration_s=1.0,
        command=("claude", "-p", "<prompt>"),
        timestamp="2026-08-07T00:00:00+00:00",
        tokens=tokens,
        prompt_sha256=seed * 64,
        phase="build",
    )


def _owned_repo(tmp_path: Path, *records: str) -> Path:
    """A flipped checkout whose ledger already holds *records*, open.

    Seeded through the kit rather than through `br create`, because a repo that has to
    spawn br to acquire its own records could not be the subject of a test about tracker
    being absent.
    """
    repo = _repo(tmp_path, tracker.MODE_OWNED)
    kit = tracker.kit(repo)
    kit.events.append(
        tracker.ledger_dir(repo),
        [
            kit.events.Draft(record, kit.events.KIND_STATUS, {"status": "open"})
            for record in records
        ],
    )
    return repo


def test_the_engine_carries_every_marker_family_with_br_absent(tmp_path: Path) -> None:
    """The acceptance criterion, driven through the engine's own API rather than the seam.

    Each family is written and then read back by the function the loop actually calls —
    `approve_checkpoint`/`checkpoint_approved`, `spend_gate_override`/`gate_override_spent`
    and `record_unreliable_gate`/`unreliable_gate_events`, `issue_grant_guarded`/
    `active_grant`, `record_rework`/`rework_charged`, `record_marker`/`tracker_history`.
    Going through `policy` and `run_record` rather than through `tracker.add_comment` is the
    point: what has to survive the flip is the engine, and a seam-level round trip would
    pass while a caller still spawned br at its own call site.

    Two beads rather than one, because the dispatch record is read back through the
    *whole-tracker* query: keyed wrong, one bead's history reads as every bead's and the
    per-bead assertion below would not notice.
    """
    repo = _owned_repo(tmp_path, "seam-0001", "seam-0002")
    config = PolicyConfig(required_gates=("verify",), max_rework=2, autonomy="L3")

    policy.approve_checkpoint(repo, "seam-0001", "ship")
    assert policy.spend_gate_override(repo, "seam-0001", "verify") is True
    policy.record_unreliable_gate(repo, "seam-0001", "verify", "passed unchanged")
    grant = policy.issue_grant_guarded(repo, "seam-0001", "L3", 8_000_000, config, interactive=True)
    charged = policy.record_rework(repo, "seam-0001", "verify")
    ident = run_record.record_marker(repo, "seam-0001", _run("a", tokens=1234))
    other = run_record.record_marker(repo, "seam-0002", _run("c", tokens=99))

    assert policy.checkpoint_approved(repo, "seam-0001", "ship") is True
    assert policy.gate_override_spent(repo, "seam-0001", "verify") is True
    assert policy.unreliable_gate_events(repo, "seam-0001", "verify") == 1
    assert grant.status == "approved"
    active = policy.active_grant(repo, "seam-0001")
    assert active is not None
    assert (active.level, active.token_budget) == ("L3", 8_000_000)
    assert charged == 1
    assert policy.rework_charged(repo, "seam-0001", "verify") == 1
    assert ident is not None and other is not None
    history = run_record.tracker_history(repo)
    assert [entry["tokens"] for entry in history["seam-0001"]] == [1234]
    assert [entry["tokens"] for entry in history["seam-0002"]] == [99]
    # The families do not bleed into each other: seam-0002 carries only its dispatch.
    assert policy.checkpoint_approved(repo, "seam-0002", "ship") is False


def test_a_second_dispatch_record_is_told_from_the_first_without_br(tmp_path: Path) -> None:
    """The dispatch record's idempotency read is the seam's, not br's.

    `record_marker` derives its id from the prompt and phase and then asks the tracker
    which ids are already recorded, so a re-dispatch is a *second* entry rather than a
    duplicate of the first. That read used to be a `comments list` spawn; if it came back
    empty after the flip the two dispatches would collapse into one id and the attempt
    count — what rework is charged against — would silently understate itself.
    """
    repo = _owned_repo(tmp_path, "seam-0001")
    record = _run("b", tokens=10)

    first = run_record.record_marker(repo, "seam-0001", record)
    second = run_record.record_marker(repo, "seam-0001", record)

    assert first != second
    assert len(run_record.tracker_history(repo)["seam-0001"]) == 2


def test_the_marker_stamp_survives_the_flip_so_a_wait_stays_measurable(tmp_path: Path) -> None:
    """The wait clock reads the *tracker's* stamp, and both stores have to supply one.

    br stamps a comment ``created_at``; the owned ledger stamps the event ``ts``. The
    seam renders one into the other, and this is the assertion that it is a real,
    parseable stamp rather than an empty string — an unparseable start is recorded as no
    wait at all (`policy.record_wait`), so the whole human-wait rollup would go quietly
    to zero without ever failing.
    """
    repo = _owned_repo(tmp_path, "seam-0001")

    wait_id = policy.record_wait_request(repo, "seam-0001", "ship")
    assert wait_id is not None
    event = policy.record_checkpoint_wait(repo, "seam-0001", "ship", by="human", delegated=False)

    assert event is not None
    assert event.wait_id == wait_id
    assert event.requested_at  # the ledger's own stamp, not the reader's clock
    assert policy.wait_events(repo, "seam-0001")[0].wait_id == wait_id


def test_a_marker_write_is_still_refused_inside_a_read_only_section(tmp_path: Path) -> None:
    """The read-only guard survives the flip, and is checked at the seam rather than below.

    Its two recorded incidents were both tracker writes a pre-flight gate should not have
    made, and neither store can delete a comment once recorded — so a flip that moved the
    write out from under `run_br` would have removed the guard along with the spawn. This
    is the assertion that it did not: nothing here installs a br at all, so the only place
    left to refuse is :func:`basicly.tracker.add_comment` itself.
    """
    repo = _owned_repo(tmp_path, "seam-0001")

    with (
        tracker.read_only("a pre-flight gate"),
        pytest.raises(tracker.TrackerWriteRefusedError) as excinfo,
    ):
        tracker.add_comment(repo, "seam-0001", "[harness-policy] recorded from a gate")

    assert "a pre-flight gate" in str(excinfo.value)
    assert tracker.read_comments(repo, "seam-0001") == []


def test_the_soft_marker_write_is_refused_too(tmp_path: Path) -> None:
    """Soft means "tolerates a store that cannot answer", never "tolerates the ban".

    The dispatch record and the spend rollup both write through the soft entry point, and
    both run inside the loop's gates — a refusal swallowed into ``False`` there would read
    as "the tracker was busy" and the gate's promise would be broken silently.
    """
    repo = _owned_repo(tmp_path, "seam-0001")

    with tracker.read_only("a pre-flight gate"), pytest.raises(tracker.TrackerWriteRefusedError):
        tracker.try_add_comment(repo, "seam-0001", "[harness-run] id=x phase=build")


def test_a_tombstoned_records_markers_read_as_absent(tmp_path: Path) -> None:
    """Same rule as :func:`basicly.tracker.owned_record`, at the marker read.

    A deleted bead's rework counter must not still be charging: the two stores spell
    absence differently and the seam is where they are made to agree, once.
    """
    repo = _owned_repo(tmp_path, "seam-0001")
    tracker.add_comment(repo, "seam-0001", "[harness-policy] rework gate=verify")
    kit = tracker.kit(repo)
    kit.events.append(
        tracker.ledger_dir(repo), [kit.events.Draft("seam-0001", kit.events.KIND_TOMBSTONE, {})]
    )

    assert tracker.read_comments(repo, "seam-0001") == []
    assert tracker.all_comment_texts(repo) == {}


def test_a_counter_refuses_to_read_a_store_that_cannot_answer(tmp_path: Path) -> None:
    """A tracker that will not load must not read as "no markers recorded".

    Every family behind :func:`basicly.tracker.read_comments` is a counter or a refusal, so the
    fail-open direction is the dangerous one: an unreadable store answering ``[]`` reads
    as zero rework attempts charged and nothing blocking, and the loop advances past the
    gate the marker existed to hold. The soft reader is the one allowed to answer empty,
    and it is asserted here beside the hard one so the split is a comparison.
    """
    repo = _repo(tmp_path, tracker.MODE_OWNED)
    for source in (repo / tracker.KIT_TRACKER_DIR).glob("*.py"):
        source.unlink()

    with pytest.raises(RuntimeError):
        tracker.read_comments(repo, "seam-0001")
    assert tracker.try_read_comments(repo, "seam-0001") == []
    assert tracker.all_comment_texts(repo) == {}
