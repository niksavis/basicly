"""The flag that says a re-record is deliberate (basicly-z9bggw).

Every claim is made against a folded record rather than an event count, because the defect
this fixes was invisible in the count: the write was skipped, the seam reported it, and the
field kept the value the caller had just moved it off.

The controls are the point. A sequence driven with the flag is compared against the same
sequence without it, which must still be swallowed, so a test that passed because the ledger
had simply become writable would fail the control.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from basicly import owned_store, owned_write, re_record, tracker, tracker_argv
from tests.test_owned_write import events_of, no_br, owned_repo, seed

__all__ = ["no_br"]  # re-exported so the fixture resolves in this module

RECORD = "wpc-1"
SRC = Path(__file__).resolve().parent.parent / "src" / "basicly"


def titles(repo: Path) -> list[str]:
    """Every title this ledger recorded for :data:`RECORD`, in the order it took them."""
    kit = owned_store.kit(repo)
    return [
        str(event.payload["value"])
        for event in events_of(repo, RECORD)
        if event.kind == kit.events.KIND_FIELD and event.payload.get("name") == "title"
    ]


def folded_title(repo: Path) -> str:
    """The title :data:`RECORD` reads as now."""
    kit = owned_store.kit(repo)
    state = kit.events.fold(kit.events.read_events(owned_store.ledger_dir(repo))[0])
    return str(state.records[RECORD].fields["title"])


# --- the flag -----------------------------------------------------------------


@pytest.mark.usefixtures("no_br")
def test_a_field_driven_back_to_a_value_it_held_folds_to_that_value(tmp_path: Path) -> None:
    """A then B then A, which is the whole bug: without the flag the third write vanished."""
    repo = owned_repo(tmp_path)
    seed(repo, RECORD)

    owned_write.append(repo, ["update", RECORD, "--title", "one"])
    owned_write.append(repo, ["update", RECORD, "--title", "two"])
    landed = owned_write.append(repo, ["update", RECORD, "--title", "one", "--again"])

    assert landed
    assert titles(repo) == ["one", "two", "one"]
    assert folded_title(repo) == "one"


@pytest.mark.usefixtures("no_br")
def test_the_same_sequence_without_the_flag_records_the_recurrence_too(
    tmp_path: Path,
) -> None:
    """The flag stopped being the only way through for a field (basicly-bj8kks).

    A state the record has moved off is re-stated about *now*, so the seam derives the
    generation instead of waiting to be told. What the flag is still for is the kinds that
    accumulate and the immediate repeat below, neither of which this rule touches.
    """
    repo = owned_repo(tmp_path)
    seed(repo, RECORD)

    owned_write.append(repo, ["update", RECORD, "--title", "one"])
    owned_write.append(repo, ["update", RECORD, "--title", "two"])
    landed = owned_write.append(repo, ["update", RECORD, "--title", "one"])

    assert landed
    assert titles(repo) == ["one", "two", "one"]
    assert folded_title(repo) == "one"


@pytest.mark.usefixtures("no_br")
def test_an_identical_write_run_twice_appends_once(tmp_path: Path) -> None:
    """The second control: a replay is still idempotent, which is what the digest is for."""
    repo = owned_repo(tmp_path)
    seed(repo, RECORD)

    owned_write.append(repo, ["update", RECORD, "--title", "one"])
    landed = owned_write.append(repo, ["update", RECORD, "--title", "one"])

    assert not landed
    assert titles(repo) == ["one"]


@pytest.mark.usefixtures("no_br")
def test_the_flag_appends_every_time_it_is_run(tmp_path: Path) -> None:
    """Stated rather than hidden: making it idempotent would reintroduce the swallow."""
    repo = owned_repo(tmp_path)
    seed(repo, RECORD)

    for _ in range(3):
        assert owned_write.append(repo, ["update", RECORD, "--title", "one", "--again"])

    assert titles(repo) == ["one", "one", "one"]


@pytest.mark.usefixtures("no_br")
def test_a_gate_that_ran_twice_and_passed_twice_is_recorded_twice(tmp_path: Path) -> None:
    """`gate report` carries neither note nor actor in its payload, so it is a plain A, A."""
    repo = owned_repo(tmp_path)
    seed(repo, RECORD)
    argv = ["gate", "report", "--gate", "verify", "--provider", "engine", "--status", "pass"]

    owned_write.append(repo, [*argv, "--note", "first run", RECORD])
    landed = owned_write.append(repo, [*argv, "--note", "second run", RECORD, "--again"])

    kit = owned_store.kit(repo)
    assert landed
    assert len([e for e in events_of(repo, RECORD) if e.kind == kit.KIND_GATE]) == 2


# --- the generation the scrub has to re-derive ---------------------------------


@pytest.mark.usefixtures("no_br")
def test_a_repeat_takes_the_next_generation_rather_than_an_arbitrary_one(
    tmp_path: Path,
) -> None:
    """The suffix is visible in the id, so the second and third occurrences are -2 and -3."""
    repo = owned_repo(tmp_path)
    seed(repo, RECORD)

    for _ in range(3):
        owned_write.append(repo, ["update", RECORD, "--title", "one", "--again"])

    minted = [e.id for e in events_of(repo, RECORD) if e.payload.get("name") == "title"]
    assert [event_id.rsplit("-", 1)[-1] for event_id in minted[1:]] == ["2", "3"]


@pytest.mark.usefixtures("no_br")
def test_the_scrub_that_re_derives_every_generation_does_not_diverge(tmp_path: Path) -> None:
    """`tracker.scrub_ledger` runs on the commit path and rewrites nothing if one id fails."""
    repo = owned_repo(tmp_path)
    seed(repo, RECORD)
    owned_write.append(repo, ["update", RECORD, "--title", "one"])
    owned_write.append(repo, ["update", RECORD, "--title", "two"])
    owned_write.append(repo, ["update", RECORD, "--title", "one", "--again"])

    assert tracker.scrub_ledger(repo) == 0

    # The positive control: the assertion above is only evidence if this scrub reads ids at
    # all, and a green pass over an empty file list looks identical.
    log = next(owned_store.ledger_dir(repo).glob("events-*.jsonl"))
    log.write_text(log.read_text(encoding="utf-8").replace("#ev-", "#ev-ff", 1), encoding="utf-8")
    with pytest.raises(owned_store.TrackerDivergenceError, match="does not re-mint"):
        tracker.scrub_ledger(repo)


# --- the flag nobody meant to type --------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ["close", RECORD, "--agian"],
        ["close", RECORD, "--reason", "done", "--json"],
        ["dep", "add", RECORD, "wpc-2", "-t", "blocks", "--agian"],
        ["dep", "remove", RECORD, "wpc-2", "-t", "blocks", "--agian"],
        ["gate", "report", "--gate", "g", "--provider", "p", "--agian", RECORD],
    ],
)
@pytest.mark.usefixtures("no_br")
def test_a_flag_the_verb_reads_nothing_from_is_refused(tmp_path: Path, argv: list[str]) -> None:
    """These four dropped it silently, so a misspelling degraded back into the swallow."""
    repo = owned_repo(tmp_path)
    seed(repo, RECORD, "wpc-2")
    before = len(events_of(repo, RECORD))

    with pytest.raises(owned_store.TrackerDivergenceError, match="reads nothing from"):
        owned_write.append(repo, argv)

    assert len(events_of(repo, RECORD)) == before


@pytest.mark.usefixtures("no_br")
def test_the_repeat_flag_itself_is_not_read_as_a_misspelling(tmp_path: Path) -> None:
    """The guard is derived from the flag tables, so the one flag it adds has to pass it."""
    repo = owned_repo(tmp_path)
    seed(repo, RECORD)

    owned_write.append(repo, ["close", RECORD, "--reason", "shipped", "--again"])

    assert owned_write.append(repo, ["close", RECORD, "--reason", "shipped", "--again"])


def test_a_verb_that_answers_for_its_own_flags_is_left_to_do_it() -> None:
    """`update` refuses an unknown flag in its translator; guarding it twice would mask that."""
    assert re_record.read_the_seams_own_flags(["update", RECORD, "--nonsense"])[0] == [
        "update",
        RECORD,
        "--nonsense",
    ]


def test_no_engine_path_passes_the_flag() -> None:
    """A state re-entered on every advance must still record one event, so no caller asks.

    Both spellings, because a census of the literal alone is fail-open: a path passing
    ``tracker_argv.REPEAT_FLAG`` carries no ``"--again"`` to find. The three modules below
    read or advertise it and none puts it in an argv, so a fourth is a decision to argue
    for rather than one to make silently.
    """
    literal = f'"{tracker_argv.REPEAT_FLAG}"'
    sources = {path.name: path.read_text(encoding="utf-8") for path in sorted(SRC.glob("*.py"))}

    assert {name for name, text in sources.items() if literal in text} == {"tracker_argv.py"}
    assert {name for name, text in sources.items() if "REPEAT_FLAG" in text} == {
        "tracker_argv.py",  # declares it
        "re_record.py",  # strips it off an argv
        "tracker_write.py",  # names it to the operator whose write was just swallowed
    }
