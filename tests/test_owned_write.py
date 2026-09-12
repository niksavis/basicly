from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from basicly import config, label_source, mirror, owned_store, owned_write, tracker

REPO_ROOT = Path(__file__).resolve().parent.parent
KIT_SOURCE = REPO_ROOT / ".basicly" / "core" / "kit" / "tracker"
PARENT = "wpc-1"


def owned_repo(tmp_path: Path, mode: str = owned_store.MODE_OWNED) -> Path:

    target = tmp_path / owned_store.KIT_TRACKER_DIR
    target.mkdir(parents=True, exist_ok=True)
    for source in sorted(KIT_SOURCE.glob("*.py")):
        shutil.copy2(source, target / source.name)
    (tmp_path / owned_store.LEDGER_DIR).mkdir(parents=True, exist_ok=True)
    (tmp_path / "basicly.toml").write_text(f'[tracker]\nmode = "{mode}"\n', encoding="utf-8")
    assert config.load_tracker_mode(tmp_path) == mode
    return tmp_path


def seed(repo: Path, *records: str) -> None:
    kit = owned_store.kit(repo)
    kit.events.append(
        owned_store.ledger_dir(repo),
        [
            kit.events.Draft(record, kit.events.KIND_STATUS, {"status": "open"})
            for record in records
        ],
    )


@pytest.fixture
def no_br(monkeypatch: pytest.MonkeyPatch) -> None:

    def refuse(cmd: list[str], **_kwargs: object) -> None:
        pytest.fail(f"the engine spawned a process after the flip: {cmd}")

    monkeypatch.setattr(subprocess, "run", refuse)


def events_of(repo: Path, record: str) -> list[Any]:
    kit = owned_store.kit(repo)
    return [
        event for event in kit.read_ledger(owned_store.ledger_dir(repo)) if event.record == record
    ]


@pytest.mark.usefixtures("no_br")
def test_a_field_write_lands_stamped_as_the_engines_own(tmp_path: Path) -> None:
    repo = owned_repo(tmp_path)
    seed(repo, PARENT)

    owned_write.append(repo, ["update", PARENT, "-t", "feature"])

    kit = owned_store.kit(repo)
    field = next(e for e in events_of(repo, PARENT) if e.kind == kit.events.KIND_FIELD)
    assert field.payload["name"] == "issue_type"
    assert field.payload["value"] == "feature"
    assert field.payload[kit.migrate.PROVENANCE_KEY] == owned_write.OWNED_PROVENANCE


@pytest.mark.usefixtures("no_br")
def test_a_flagless_update_is_refused_rather_than_reported_as_recorded(tmp_path: Path) -> None:
    repo = owned_repo(tmp_path)
    seed(repo, PARENT)
    before = len(events_of(repo, PARENT))

    with pytest.raises(owned_store.TrackerDivergenceError, match="states nothing"):
        owned_write.append(repo, ["update", PARENT])

    assert len(events_of(repo, PARENT)) == before


@pytest.mark.usefixtures("no_br")
def test_a_write_that_legitimately_records_nothing_is_left_alone(tmp_path: Path) -> None:
    repo = owned_repo(tmp_path)
    seed(repo, PARENT)

    for verb in sorted(mirror.UNMIRRORED_WRITES):
        owned_write.append(repo, [verb])


@pytest.mark.usefixtures("no_br")
def test_a_write_with_no_translation_stops_the_work(tmp_path: Path) -> None:
    repo = owned_repo(tmp_path)
    seed(repo, PARENT)

    with pytest.raises(owned_store.TrackerDivergenceError) as refusal:
        owned_write.append(repo, ["reopen", PARENT])

    assert "'reopen'" in str(refusal.value)
    assert "comments add" in str(refusal.value)


@pytest.mark.usefixtures("no_br")
def test_a_create_mints_a_child_id_and_records_the_whole_record(tmp_path: Path) -> None:
    repo = owned_repo(tmp_path)
    seed(repo, PARENT)
    kit = owned_store.kit(repo)

    record = owned_write.create(
        repo,
        ["create", "a child", "-t", "task", "--parent", PARENT, "-l", "phase-6,ready", "--json"],
    )

    assert record == f"{PARENT}.1"
    assert [event.kind for event in events_of(repo, record)] == [
        kit.events.KIND_CREATED,
        kit.events.KIND_STATUS,
        kit.migrate.KIND_EDGE,
    ]
    created, status, edge = events_of(repo, record)
    assert created.payload["title"] == "a child"
    assert created.payload["issue_type"] == "task"
    assert (tracker.read_record(repo, record) or {})["labels"] == ["phase-6", "ready"]
    assert status.payload["status"] == "open"
    assert edge.payload[kit.migrate.EDGE_TO] == PARENT
    assert edge.payload[kit.migrate.EDGE_TYPE] == kit.DEFAULT_VOCABULARY.parent_child_type


@pytest.mark.usefixtures("no_br")
def test_two_creates_under_one_parent_get_distinct_ids(tmp_path: Path) -> None:
    repo = owned_repo(tmp_path)
    seed(repo, PARENT)
    argv = ["create", "a child", "-t", "task", "--parent", PARENT, "--json"]

    first = owned_write.create(repo, [*argv])
    second = owned_write.create(repo, ["create", "another", "-t", "task", "--parent", PARENT])

    assert [first, second] == [f"{PARENT}.1", f"{PARENT}.2"]


@pytest.mark.usefixtures("no_br")
def test_a_create_naming_no_parent_is_refused_when_no_prefix_is_declared(
    tmp_path: Path,
) -> None:

    repo = owned_repo(tmp_path)

    with pytest.raises(owned_store.TrackerDivergenceError, match=r"declares\s+none"):
        owned_write.create(repo, ["create", "a root", "-t", "epic", "--json"])
    assert owned_store.kit(repo).read_ledger(owned_store.ledger_dir(repo)) == []


@pytest.mark.usefixtures("no_br")
def test_a_declared_prefix_mints_a_root(tmp_path: Path) -> None:

    repo = owned_repo(tmp_path)
    (repo / "basicly.toml").write_text('[tracker]\nmode = "owned"\nprefix = "wpc"\n', "utf-8")

    record = owned_write.create(repo, ["create", "a root", "-t", "epic", "--json"])

    assert record.startswith("wpc-") and "." not in record
    kit = owned_store.kit(repo)
    events = kit.read_ledger(owned_store.ledger_dir(repo))
    assert {event.record for event in events} == {record}
    assert kit.events.KIND_CREATED in {event.kind for event in events}


@pytest.mark.usefixtures("no_br")
def test_a_child_of_a_child_nests_rather_than_flattening(tmp_path: Path) -> None:
    repo = owned_repo(tmp_path)
    seed(repo, PARENT)
    child = owned_write.create(repo, ["create", "c", "-t", "task", "--parent", PARENT, "--json"])

    grandchild = owned_write.create(repo, ["create", "g", "-t", "task", "--parent", child])

    assert grandchild == f"{PARENT}.1.1"


def edges_of(repo: Path, record: str) -> list[tuple[str, str]]:
    kit = owned_store.kit(repo)
    views = kit.views_from_events(kit.read_ledger(owned_store.ledger_dir(repo)))
    view = views.get(record)
    return [(edge.target, edge.type) for edge in (view.dependencies if view else ())]


@pytest.mark.usefixtures("no_br")
def test_a_dep_remove_folds_the_edge_away_and_leaves_both_events_in_the_log(
    tmp_path: Path,
) -> None:

    repo = owned_repo(tmp_path)
    seed(repo, "wpc-1.1", "wpc-1.2")
    owned_write.append(repo, ["dep", "add", "wpc-1.2", "wpc-1.1", "-t", "blocks"])
    assert edges_of(repo, "wpc-1.2") == [("wpc-1.1", "blocks")]

    owned_write.append(repo, ["dep", "remove", "wpc-1.2", "wpc-1.1", "-t", "blocks"])

    assert edges_of(repo, "wpc-1.2") == []
    kit = owned_store.kit(repo)
    kinds = [event.kind for event in events_of(repo, "wpc-1.2")]
    assert kinds.count(kit.migrate.KIND_EDGE) == 1
    assert kinds.count(kit.events.KIND_EDGE_RETRACTED) == 1


@pytest.mark.parametrize(
    ("target", "edge_type"),
    [("wpc-1.3", "blocks"), ("wpc-1.1", "related")],
    ids=["a target nothing points at", "the right pair under the wrong type"],
)
@pytest.mark.usefixtures("no_br")
def test_a_dep_remove_absent_from_the_ledger_is_refused_and_records_nothing(
    tmp_path: Path, target: str, edge_type: str
) -> None:

    repo = owned_repo(tmp_path)
    seed(repo, "wpc-1.1", "wpc-1.2")
    owned_write.append(repo, ["dep", "add", "wpc-1.2", "wpc-1.1", "-t", "blocks"])
    before = len(events_of(repo, "wpc-1.2"))

    with pytest.raises(owned_store.TrackerDivergenceError) as refusal:
        owned_write.append(repo, ["dep", "remove", "wpc-1.2", target, "-t", edge_type])

    assert "nothing to retract" in str(refusal.value)
    assert target in str(refusal.value)
    assert "wpc-1.2" in str(refusal.value)
    assert len(events_of(repo, "wpc-1.2")) == before
    assert edges_of(repo, "wpc-1.2") == [("wpc-1.1", "blocks")]


def labels_of(repo: Path, record: str) -> list[str]:

    return list((tracker.read_record(repo, record) or {}).get("labels") or [])


@pytest.mark.usefixtures("no_br")
def test_add_label_accumulates_against_the_set_the_record_already_holds(tmp_path: Path) -> None:
    repo = owned_repo(tmp_path)
    seed(repo, PARENT)

    owned_write.append(repo, ["update", PARENT, "--add-label", "cut-a"])
    owned_write.append(repo, ["update", PARENT, "--add-label", "cut-b"])

    assert labels_of(repo, PARENT) == ["cut-a", "cut-b"]


@pytest.mark.usefixtures("no_br")
def test_remove_label_drops_one_and_leaves_the_rest(tmp_path: Path) -> None:
    repo = owned_repo(tmp_path)
    seed(repo, PARENT)
    owned_write.append(repo, ["update", PARENT, "--add-label", "cut-a,cut-b,cut-c"])

    owned_write.append(repo, ["update", PARENT, "--remove-label", "cut-b"])

    assert labels_of(repo, PARENT) == ["cut-a", "cut-c"]


@pytest.mark.usefixtures("no_br")
def test_a_repeated_add_does_not_duplicate_the_label(tmp_path: Path) -> None:
    repo = owned_repo(tmp_path)
    seed(repo, PARENT)

    owned_write.append(repo, ["update", PARENT, "--add-label", "cut-a"])
    owned_write.append(repo, ["update", PARENT, "--add-label", "cut-a"])

    assert labels_of(repo, PARENT) == ["cut-a"]


@pytest.mark.usefixtures("no_br")
def test_a_label_write_carries_the_other_flags_of_the_same_update(tmp_path: Path) -> None:
    repo = owned_repo(tmp_path)
    seed(repo, PARENT)

    owned_write.append(repo, ["update", PARENT, "--add-label", "cut-a", "-p", "1"])

    record = tracker.read_record(repo, PARENT) or {}
    assert record["labels"] == ["cut-a"]
    assert record["priority"] == 1


@pytest.mark.usefixtures("no_br")
def test_the_labelled_query_finds_a_record_this_seam_labelled(tmp_path: Path) -> None:
    repo = owned_repo(tmp_path)
    seed(repo, PARENT)

    owned_write.append(repo, ["update", PARENT, "--add-label", "cut-a"])

    assert label_source.labelled(repo, "cut-a") == {PARENT: "open"}
    assert label_source.labelled(repo, "cut-b") == {}


@pytest.mark.usefixtures("no_br")
def test_a_label_write_naming_two_records_is_refused(tmp_path: Path) -> None:
    repo = owned_repo(tmp_path)
    seed(repo, PARENT, "wpc-2")

    with pytest.raises(owned_store.TrackerDivergenceError, match="one write per record"):
        owned_write.append(repo, ["update", PARENT, "wpc-2", "--add-label", "cut-a"])


@pytest.mark.usefixtures("no_br")
def test_the_seam_refuses_a_create_inside_a_read_only_section(tmp_path: Path) -> None:

    repo = owned_repo(tmp_path)
    seed(repo, PARENT)

    with tracker.read_only("a pre-flight gate"), pytest.raises(tracker.TrackerWriteRefusedError):
        tracker.create_record(repo, ["create", "c", "-t", "task", "--parent", PARENT, "--json"])
    assert events_of(repo, f"{PARENT}.1") == []
