"""Which dependency edges the hand-write repair may append, and what it writes for one.

The half of `basicly-vkh0.32` that `test_tracker_adoption.py` cannot discriminate: that file
drives the whole repair through `br.adopt_hand_writes` and asks whether the ledger caught
up, so a run that repaired *every* record's edges would pass it. Scope is the claim here —
history excused, an adopted record left to the record-level import, a record the ledger has
no event for left to it as well — and the exact payload an appended edge carries.

The kit is the shipped one, copied into a tmp checkout: `shortfall` reads three of its
vocabularies (`views_from_events`, `migrate`'s payload keys, `events.Draft`) and a stub
would be a second spelling of all three. Nothing here spawns a process or reads the host's
tracker.
"""

from __future__ import annotations

import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from basicly import br, edge_adoption, mirror

REPO_ROOT = Path(__file__).resolve().parent.parent
KIT_SOURCE = REPO_ROOT / ".basicly" / "core" / "kit" / "tracker"

EDGE = ("basicly-x", "blocks")


@pytest.fixture
def installed(tmp_path: Path) -> tuple[Any, Any, Path]:
    """The shipped kit and its baseline module, over an empty ledger directory."""
    (tmp_path / br.KIT_TRACKER_DIR).mkdir(parents=True)
    for source in sorted(KIT_SOURCE.glob("*.py")):
        shutil.copy2(source, tmp_path / br.KIT_TRACKER_DIR / source.name)
    return br.kit(tmp_path), br.kit(tmp_path, br.BASELINE_MODULE), tmp_path / br.LEDGER_DIR


def _ledger(
    kit_module: Any, directory: Path, created: Mapping[str, Mapping[str, str]]
) -> list[Any]:
    """A ledger holding one ``created`` event per record, carrying the payload given."""
    kit_module.events.append(
        directory,
        [
            kit_module.events.Draft(record, kit_module.events.KIND_CREATED, dict(payload))
            for record, payload in created.items()
        ],
    )
    return kit_module.read_ledger(directory)


def test_only_a_record_the_dual_write_created_is_repaired_at_its_edges(
    installed: tuple[Any, Any, Path],
) -> None:
    """The three records the flip boundary keeps this repair away from, in one population.

    An imported record's disagreements are excused as history, an adopted one has its edges
    written by the record-level import — twice, if this wrote them too — and a record the
    ledger holds no event for is that import's to create. Each is asked here against a
    reference that states the same missing edge for all three.
    """
    kit_module, boundary, directory = installed
    events = _ledger(
        kit_module,
        directory,
        {
            "basicly-a": {boundary.IMPORT_MARKER: br.IMPORT_SOURCE},
            "basicly-b": {boundary.IMPORT_MARKER: boundary.ADOPTION_SOURCE},
        },
    )
    reference = {record: {EDGE} for record in ("basicly-a", "basicly-b", "basicly-c")}

    assert (
        edge_adoption.shortfall(kit_module, boundary, events, reference, reference)
        == edge_adoption.EdgeShortfall()
    )


def test_an_adopted_edge_carries_the_extracted_provenance_and_the_kits_own_payload_keys(
    installed: tuple[Any, Any, Path],
) -> None:
    """A fact recorded under a key the kit does not read is a fact the ledger does not hold.

    The marker matters as much as the keys: `baseline.origins` classifies a record by the
    source on its ``created`` event, so an edge stamped with anything else would leave the
    run's own repair unattributable.
    """
    kit_module, boundary, directory = installed
    events = _ledger(kit_module, directory, {"basicly-d": {"provenance": mirror.MIRROR_PROVENANCE}})

    short = edge_adoption.shortfall(
        kit_module, boundary, events, {"basicly-d": {EDGE}}, {"basicly-d": {EDGE}}
    )

    assert short.adopted == (("basicly-d", "basicly-x", "blocks"),)
    (draft,) = short.drafts
    migrate = kit_module.migrate
    assert draft.record == "basicly-d"
    assert draft.kind == migrate.KIND_EDGE
    assert draft.payload == {
        migrate.PROVENANCE_KEY: migrate.EXTRACTED,
        migrate.SOURCE_KEY: boundary.ADOPTION_SOURCE,
        migrate.EDGE_FROM: "basicly-d",
        migrate.EDGE_TO: "basicly-x",
        migrate.EDGE_TYPE: "blocks",
    }


def test_an_edge_the_export_does_not_carry_is_named_and_never_written(
    installed: tuple[Any, Any, Path],
) -> None:
    """Detection is the reference's, repair is the export's, and the gap between them is said.

    Writing the reference's own edge instead would make the differential agree about it by
    construction — the same reason the record-level repair imports from the committed export.
    """
    kit_module, boundary, directory = installed
    events = _ledger(kit_module, directory, {"basicly-d": {"provenance": mirror.MIRROR_PROVENANCE}})

    short = edge_adoption.shortfall(kit_module, boundary, events, {"basicly-d": {EDGE}}, {})

    assert short == edge_adoption.EdgeShortfall(unexported=("basicly-d",))
