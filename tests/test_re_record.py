from __future__ import annotations

from pathlib import Path

import pytest

from basicly import owned_store, owned_write, re_record, tracker, tracker_argv
from tests.test_owned_write import events_of, no_br, owned_repo, seed

__all__ = ["no_br"]

RECORD = "wpc-1"
SRC = Path(__file__).resolve().parent.parent / "src" / "basicly"


def titles(repo: Path) -> list[str]:
    kit = owned_store.kit(repo)
    return [
        str(event.payload["value"])
        for event in events_of(repo, RECORD)
        if event.kind == kit.events.KIND_FIELD and event.payload.get("name") == "title"
    ]


def folded_title(repo: Path) -> str:
    kit = owned_store.kit(repo)
    state = kit.events.fold(kit.events.read_events(owned_store.ledger_dir(repo))[0])
    return str(state.records[RECORD].fields["title"])


@pytest.mark.usefixtures("no_br")
def test_a_field_driven_back_to_a_value_it_held_folds_to_that_value(tmp_path: Path) -> None:
    repo = owned_repo(tmp_path)
    seed(repo, RECORD)

    owned_write.append(repo, ["update", RECORD, "--title", "one"])
    owned_write.append(repo, ["update", RECORD, "--title", "two"])
    _, landed = owned_write.append(repo, ["update", RECORD, "--title", "one", "--again"])

    assert landed
    assert titles(repo) == ["one", "two", "one"]
    assert folded_title(repo) == "one"


@pytest.mark.usefixtures("no_br")
def test_the_same_sequence_without_the_flag_records_the_recurrence_too(
    tmp_path: Path,
) -> None:

    repo = owned_repo(tmp_path)
    seed(repo, RECORD)

    owned_write.append(repo, ["update", RECORD, "--title", "one"])
    owned_write.append(repo, ["update", RECORD, "--title", "two"])
    _, landed = owned_write.append(repo, ["update", RECORD, "--title", "one"])

    assert landed
    assert titles(repo) == ["one", "two", "one"]
    assert folded_title(repo) == "one"


@pytest.mark.usefixtures("no_br")
def test_an_identical_write_run_twice_appends_once(tmp_path: Path) -> None:
    repo = owned_repo(tmp_path)
    seed(repo, RECORD)

    owned_write.append(repo, ["update", RECORD, "--title", "one"])
    _, landed = owned_write.append(repo, ["update", RECORD, "--title", "one"])

    assert not landed
    assert titles(repo) == ["one"]


@pytest.mark.usefixtures("no_br")
def test_the_flag_appends_every_time_it_is_run(tmp_path: Path) -> None:
    repo = owned_repo(tmp_path)
    seed(repo, RECORD)

    for _ in range(3):
        assert owned_write.append(repo, ["update", RECORD, "--title", "one", "--again"])[1]

    assert titles(repo) == ["one", "one", "one"]


@pytest.mark.usefixtures("no_br")
def test_a_gate_that_ran_twice_and_passed_twice_is_recorded_twice(tmp_path: Path) -> None:
    repo = owned_repo(tmp_path)
    seed(repo, RECORD)
    argv = ["gate", "report", "--gate", "verify", "--provider", "engine", "--status", "pass"]

    owned_write.append(repo, [*argv, "--note", "first run", RECORD])
    _, landed = owned_write.append(repo, [*argv, "--note", "second run", RECORD, "--again"])

    kit = owned_store.kit(repo)
    assert landed
    assert len([e for e in events_of(repo, RECORD) if e.kind == kit.KIND_GATE]) == 2


@pytest.mark.usefixtures("no_br")
def test_a_repeat_takes_the_next_generation_rather_than_an_arbitrary_one(
    tmp_path: Path,
) -> None:
    repo = owned_repo(tmp_path)
    seed(repo, RECORD)

    for _ in range(3):
        owned_write.append(repo, ["update", RECORD, "--title", "one", "--again"])

    minted = [e.id for e in events_of(repo, RECORD) if e.payload.get("name") == "title"]
    assert [event_id.rsplit("-", 1)[-1] for event_id in minted[1:]] == ["2", "3"]


@pytest.mark.usefixtures("no_br")
def test_the_scrub_that_re_derives_every_generation_does_not_diverge(tmp_path: Path) -> None:
    repo = owned_repo(tmp_path)
    seed(repo, RECORD)
    owned_write.append(repo, ["update", RECORD, "--title", "one"])
    owned_write.append(repo, ["update", RECORD, "--title", "two"])
    owned_write.append(repo, ["update", RECORD, "--title", "one", "--again"])

    assert tracker.scrub_ledger(repo) == 0

    log = next(owned_store.ledger_dir(repo).glob("events-*.jsonl"))
    log.write_text(log.read_text(encoding="utf-8").replace("#ev-", "#ev-ff", 1), encoding="utf-8")
    with pytest.raises(owned_store.TrackerDivergenceError, match="does not re-mint"):
        tracker.scrub_ledger(repo)


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
    repo = owned_repo(tmp_path)
    seed(repo, RECORD, "wpc-2")
    before = len(events_of(repo, RECORD))

    with pytest.raises(owned_store.TrackerDivergenceError, match="reads nothing from"):
        owned_write.append(repo, argv)

    assert len(events_of(repo, RECORD)) == before


@pytest.mark.usefixtures("no_br")
def test_the_repeat_flag_itself_is_not_read_as_a_misspelling(tmp_path: Path) -> None:
    repo = owned_repo(tmp_path)
    seed(repo, RECORD)

    owned_write.append(repo, ["close", RECORD, "--reason", "shipped", "--again"])

    assert owned_write.append(repo, ["close", RECORD, "--reason", "shipped", "--again"])[1]


def test_a_verb_that_answers_for_its_own_flags_is_left_to_do_it() -> None:
    assert re_record.read_the_seams_own_flags(["update", RECORD, "--nonsense"])[0] == [
        "update",
        RECORD,
        "--nonsense",
    ]


def test_no_engine_path_passes_the_flag() -> None:

    literal = f'"{tracker_argv.REPEAT_FLAG}"'
    sources = {path.name: path.read_text(encoding="utf-8") for path in sorted(SRC.glob("*.py"))}

    assert {name for name, text in sources.items() if literal in text} == {"tracker_argv.py"}
    assert {name for name, text in sources.items() if "REPEAT_FLAG" in text} == {
        "tracker_argv.py",
        "re_record.py",
        "tracker_write.py",
    }
