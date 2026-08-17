"""The engine's own writes to the owned ledger, with nothing to spawn (basicly-wpc8).

Two claims, and the second is the one this bead exists for:

- an ordinary write lands stamped as the engine's own rather than as a mirrored one;
- a ``create`` mints its record id **in the ledger** and returns it, which is the surface
  the mirror could never carry — its translation reads the id out of br's reply.

Every test here runs with the binary genuinely off PATH and a spawn wired to fail the
test, because "br was absent and the write silently went nowhere" would satisfy a weaker
assertion and is exactly the failure mode this module could have.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest

from basicly import br, config, owned_store, owned_write

REPO_ROOT = Path(__file__).resolve().parent.parent
KIT_SOURCE = REPO_ROOT / ".basicly" / "core" / "kit" / "tracker"
PARENT = "wpc-1"


def owned_repo(tmp_path: Path, mode: str = owned_store.MODE_OWNED) -> Path:
    """A checkout with the tracker kit installed and ``[tracker] mode`` declared.

    The declaration is read back through :func:`config.load_tracker_mode` before the test
    runs. Not belt-and-braces: the mode reader is installed as an import side effect of
    `basicly.config` (the inversion `owned_store.set_mode_reader` documents), so a test
    module that never reached it would have every seam raise for the wrong reason.
    """
    target = tmp_path / owned_store.KIT_TRACKER_DIR
    target.mkdir(parents=True, exist_ok=True)
    for source in sorted(KIT_SOURCE.glob("*.py")):
        shutil.copy2(source, target / source.name)
    (tmp_path / owned_store.LEDGER_DIR).mkdir(parents=True, exist_ok=True)
    (tmp_path / "basicly.toml").write_text(f'[tracker]\nmode = "{mode}"\n', encoding="utf-8")
    assert config.load_tracker_mode(tmp_path) == mode
    return tmp_path


def seed(repo: Path, *records: str) -> None:
    """Open *records* in the ledger, through the kit rather than through a spawn."""
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
    """No br on PATH, and a spawn is a failure rather than a fallback."""
    monkeypatch.setattr(br, "which", lambda: None)

    def refuse(cmd: list[str], **_kwargs: object) -> None:
        pytest.fail(f"the engine spawned a process after the flip: {cmd}")

    monkeypatch.setattr(br.subprocess, "run", refuse)


def events_of(repo: Path, record: str) -> list[Any]:
    """Every ledger event naming *record*, in the order they were appended."""
    kit = owned_store.kit(repo)
    return [
        event for event in kit.read_ledger(owned_store.ledger_dir(repo)) if event.record == record
    ]


# --- the ordinary write -------------------------------------------------------


@pytest.mark.usefixtures("no_br")
def test_a_field_write_lands_stamped_as_the_engines_own(tmp_path: Path) -> None:
    """The provenance is what tells a native write from one the dual write mirrored."""
    repo = owned_repo(tmp_path)
    seed(repo, PARENT)

    owned_write.append(repo, ["update", PARENT, "-t", "feature"])

    kit = owned_store.kit(repo)
    field = next(e for e in events_of(repo, PARENT) if e.kind == kit.events.KIND_FIELD)
    assert field.payload["name"] == "issue_type"
    assert field.payload["value"] == "feature"
    assert field.payload[kit.migrate.PROVENANCE_KEY] == owned_write.OWNED_PROVENANCE


@pytest.mark.usefixtures("no_br")
def test_a_write_with_no_translation_stops_the_work(tmp_path: Path) -> None:
    """A surface nobody translated is a dependency somebody took without deciding to."""
    repo = owned_repo(tmp_path)
    seed(repo, PARENT)

    with pytest.raises(owned_store.TrackerDivergenceError, match="no owned-ledger translation"):
        owned_write.append(repo, ["reopen", PARENT])


# --- the create ---------------------------------------------------------------


@pytest.mark.usefixtures("no_br")
def test_a_create_mints_a_child_id_and_records_the_whole_record(tmp_path: Path) -> None:
    """The surface the mirror cannot carry: the id comes back from the store."""
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
    # The typed pair: a flag's value arrives as one argv string, and a stored
    # `"phase-6,ready"` iterates as twelve one-character labels at the reader.
    assert created.payload["labels"] == ["phase-6", "ready"]
    assert status.payload["status"] == "open"
    assert edge.payload[kit.migrate.EDGE_TO] == PARENT
    assert edge.payload[kit.migrate.EDGE_TYPE] == kit.DEFAULT_VOCABULARY.parent_child_type


@pytest.mark.usefixtures("no_br")
def test_two_creates_under_one_parent_get_distinct_ids(tmp_path: Path) -> None:
    """The mint reads the ledger back, so the second create cannot repeat the first."""
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
    """A root mint needs an id prefix, and a repository need not declare one.

    Refused rather than defaulted, because a guessed prefix mints an id in a namespace
    nothing else in the repository uses, and no read would find the record again.
    """
    repo = owned_repo(tmp_path)

    with pytest.raises(owned_store.TrackerDivergenceError, match=r"declares\s+none"):
        owned_write.create(repo, ["create", "a root", "-t", "epic", "--json"])
    assert owned_store.kit(repo).read_ledger(owned_store.ledger_dir(repo)) == []


@pytest.mark.usefixtures("no_br")
def test_a_declared_prefix_mints_a_root(tmp_path: Path) -> None:
    """The prefix used to live in the external tracker's config, which the flip deletes.

    So a root mint reads it from ``[tracker] prefix`` instead (basicly-vkh0.42.7). The
    assertion is the id's shape rather than its value: the root half is random by design.
    """
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
    """Any record id is a valid parent, so a grandchild keeps its own branch of the tree."""
    repo = owned_repo(tmp_path)
    seed(repo, PARENT)
    child = owned_write.create(repo, ["create", "c", "-t", "task", "--parent", PARENT, "--json"])

    grandchild = owned_write.create(repo, ["create", "g", "-t", "task", "--parent", child])

    assert grandchild == f"{PARENT}.1.1"


# --- the seam above it --------------------------------------------------------


@pytest.mark.usefixtures("no_br")
def test_the_seam_refuses_a_create_inside_a_read_only_section(tmp_path: Path) -> None:
    """A gate that promised to write nothing must not create a bead either.

    Refused at :func:`br.create_record` rather than below it, because on this rung there is
    no spawn left to inherit the guard from — the same split `br.write` makes.
    """
    repo = owned_repo(tmp_path)
    seed(repo, PARENT)

    with br.read_only("a pre-flight gate"), pytest.raises(br.TrackerWriteRefusedError):
        br.create_record(repo, ["create", "c", "-t", "task", "--parent", PARENT, "--json"])
    assert events_of(repo, f"{PARENT}.1") == []
