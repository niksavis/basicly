"""Which records carry a label (basicly-wpc8).

The query a supervised pass's lane set is assembled from. Two things need saying:

- the read answers from the folded ``labels`` field with nothing spawned, and a **closed**
  record is in the answer — a finished cut read as an empty one reports itself blocked;
- the matching write resolves an accumulation against the record's own set at the seam,
  and it is pinned here beside the read, because together they are what makes
  ``basicly loop supervise --label`` usable.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from basicly import label_source, owned_store, supervise, tracker
from tests.test_owned_write import no_br, owned_repo

__all__ = ["no_br"]  # re-exported so the fixture resolves in this module

LABEL = "phase-6"


def _proc(stdout: str) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["br"], 0, stdout, "")


def _labelled(repo: Path, records: dict[str, tuple[str, object]]) -> None:
    """Seed each record's status and its labels, through the only event that can carry them.

    A ``created`` payload, not a ``field`` event: ``events.TRUNCATABLE_KEYS`` holds
    ``value``, so a ``field`` event whose value is a list is refused by the ledger
    (measured 2026-08-17). That is the shape the import left behind, and the reason a
    later label write stores the joined form instead — both are read by
    ``tracker_argv.labels_of``.
    """
    kit = owned_store.kit(repo)
    drafts = []
    for record, (status, labels) in records.items():
        fields = {} if labels is None else {label_source.LABELS_FIELD: labels}
        drafts.append(kit.events.Draft(record, kit.events.KIND_CREATED, fields))
        drafts.append(kit.events.Draft(record, kit.events.KIND_STATUS, {"status": status}))
    kit.events.append(owned_store.ledger_dir(repo), drafts)


# --- the read -----------------------------------------------------------------


@pytest.mark.usefixtures("no_br")
def test_the_read_selects_the_labelled_records_closed_ones_included(
    tmp_path: Path,
) -> None:
    """The set and its status, with an unlabelled record as the control.

    The closed record is the load-bearing member: without it a cut whose every bead has
    landed reads as an empty selection, and the fan-in reports a finished pass as blocked.
    """
    repo = owned_repo(tmp_path)
    _labelled(
        repo,
        {
            "wpc-1.1": ("open", [LABEL]),
            "wpc-1.2": ("closed", [LABEL, "ready"]),
            "wpc-1.3": ("open", ["other"]),
            "wpc-1.4": ("open", None),
        },
    )

    assert label_source.labelled(repo, LABEL) == {"wpc-1.1": "open", "wpc-1.2": "closed"}


@pytest.mark.usefixtures("no_br")
def test_a_tombstoned_record_is_not_a_lane(tmp_path: Path) -> None:
    """The absence rule `tracker.owned_record` states: a deleted bead must not be dispatched."""
    repo = owned_repo(tmp_path)
    _labelled(repo, {"wpc-1.1": ("open", [LABEL])})
    kit = owned_store.kit(repo)
    kit.events.append(
        owned_store.ledger_dir(repo),
        [kit.events.Draft("wpc-1.1", kit.events.KIND_TOMBSTONE, {})],
    )

    assert label_source.labelled(repo, LABEL) == {}


@pytest.mark.usefixtures("no_br")
def test_a_labels_field_holding_a_bare_string_matches_the_whole_string(tmp_path: Path) -> None:
    """One label, not one per character.

    A reader that iterated the string would match ``p`` and ``h`` and hand a pass twelve
    lanes. `tracker_argv.labels_of` splits on the separator instead, which is why a label with
    no separator in it is one label.
    """
    repo = owned_repo(tmp_path)
    _labelled(repo, {"wpc-1.1": ("open", LABEL), "wpc-1.2": ("open", "p")})

    assert label_source.labelled(repo, LABEL) == {"wpc-1.1": "open"}
    assert label_source.labelled(repo, "p") == {"wpc-1.2": "open"}


# --- the read ------------------------------------------------------------------


def test_the_read_answers_from_the_fold_with_nothing_spawned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A spawn fails the test: an empty answer is what a mistyped label is refused on.

    "The store could not answer" must not read as "no bead carries this label", which
    would report a whole cut as blocked for a reason unrelated to the cut.
    """
    repo = owned_repo(tmp_path)
    _labelled(repo, {"wpc-1.1": ("open", [LABEL])})
    monkeypatch.setattr(
        subprocess, "run", lambda *_a, **_k: pytest.fail("the read spawned a process")
    )

    assert label_source.labelled(repo, LABEL) == {"wpc-1.1": "open"}


# --- the writer this read now has ---------------------------------------------


@pytest.mark.usefixtures("no_br")
def test_a_label_written_through_the_seam_is_found_by_this_read(tmp_path: Path) -> None:
    """The bound this module used to pin, lifted (basicly-wpc8).

    The refusal it replaces was real: nothing in the engine could label a lane into a cut,
    so ``loop supervise --label`` had no way to be set up. The write now resolves the
    accumulation against the record's own set at the seam, under the ledger lock.
    """
    repo = owned_repo(tmp_path)
    _labelled(repo, {"wpc-1.1": ("open", [])})

    tracker.write(repo, ["update", "wpc-1.1", "--add-label", LABEL])

    assert label_source.labelled(repo, LABEL) == {"wpc-1.1": "open"}


@pytest.mark.usefixtures("no_br")
def test_a_mistyped_label_refuses_the_pass_rather_than_deriving_an_empty_session(
    tmp_path: Path,
) -> None:
    """The consumer of this read, so the empty answer reaches the caller that acts on it."""
    repo = owned_repo(tmp_path)
    _labelled(repo, {"wpc-1.1": ("open", [LABEL])})

    with pytest.raises(supervise.LaneSelectionError, match="carries label 'phase-7'"):
        supervise.lane_selection(repo, "phase-7")
    assert supervise.lane_selection(repo, LABEL) == (("wpc-1.1", "open"),)
