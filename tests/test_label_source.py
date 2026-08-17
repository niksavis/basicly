"""Which records carry a label, rung by rung (basicly-wpc8).

The query a supervised pass's lane set is assembled from. Three things need saying and the
third is the awkward one:

- the flipped read answers from the folded ``labels`` field with nothing spawned;
- the external read needs *two* spawns, because br's default query omits ``closed`` and a
  finished cut read as an empty one reports itself blocked;
- **the matching write has no owned equivalent**, and the seam refuses it rather than
  mirroring a field it would drop. That refusal is pinned here, beside the read that
  depends on it, because it is the bound on ``basicly loop supervise --label``.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from basicly import br, label_source, owned_store, supervise
from tests.test_owned_write import no_br, owned_repo

__all__ = ["no_br"]  # re-exported so the fixture resolves in this module

LABEL = "phase-6"


def _proc(stdout: str) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["br"], 0, stdout, "")


def _labelled(repo: Path, records: dict[str, tuple[str, object]]) -> None:
    """Seed each record's status and its labels, through the only event that can carry them.

    A ``created`` payload, not a ``field`` event: ``events.TRUNCATABLE_KEYS`` holds
    ``value``, so a ``field`` event whose value is a list is refused by the ledger
    (measured 2026-08-17). That is also why the engine has no label writer — `create` is
    the one surface that can put a list there, which is what
    :func:`test_adding_a_label_is_still_refused_so_this_read_has_no_engine_side_writer`
    pins from the other side.
    """
    kit = owned_store.kit(repo)
    drafts = []
    for record, (status, labels) in records.items():
        fields = {} if labels is None else {label_source.LABELS_FIELD: labels}
        drafts.append(kit.events.Draft(record, kit.events.KIND_CREATED, fields))
        drafts.append(kit.events.Draft(record, kit.events.KIND_STATUS, {"status": status}))
    kit.events.append(owned_store.ledger_dir(repo), drafts)


# --- the flipped read ---------------------------------------------------------


@pytest.mark.usefixtures("no_br")
def test_the_flipped_read_selects_the_labelled_records_closed_ones_included(
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
    """The absence rule `br.owned_record` states: a deleted bead must not be dispatched."""
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

    The mirror types the field as a list precisely so this cannot arise; a reader that
    iterated the string anyway would match ``p`` and ``h`` and hand a pass twelve lanes.
    """
    repo = owned_repo(tmp_path)
    _labelled(repo, {"wpc-1.1": ("open", LABEL), "wpc-1.2": ("open", "p")})

    assert label_source.labelled(repo, LABEL) == {"wpc-1.1": "open"}
    assert label_source.labelled(repo, "p") == {"wpc-1.2": "open"}


# --- the external read --------------------------------------------------------


def test_the_external_read_spawns_both_queries_and_unions_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One spawn would silently drop every closed bead in the cut."""
    repo = owned_repo(tmp_path, owned_store.MODE_DUAL)
    calls: list[list[str]] = []

    def spawn(_root: Path, args: list[str], **_kwargs: object):
        calls.append(args)
        closed = "--status" in args
        record = {"id": "wpc-1.2", "status": "closed"} if closed else {"id": "wpc-1.1"}
        return _proc(json.dumps({"issues": [record]}))

    monkeypatch.setattr(br, "run_br", spawn)

    assert label_source.labelled(repo, LABEL) == {"wpc-1.1": "", "wpc-1.2": "closed"}
    assert [args[:4] for args in calls] == [
        ["list", "--label", LABEL, "--json"],
        ["list", "--label", LABEL, "--status"],
    ]


def test_the_flipped_read_answers_while_br_holds_the_other_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The comparison that makes the flip visible rather than merely consistent."""
    repo = owned_repo(tmp_path)
    _labelled(repo, {"wpc-1.1": ("open", [LABEL])})
    monkeypatch.setattr(
        br, "run_br", lambda *_a, **_k: pytest.fail("the flipped read spawned a process")
    )

    assert label_source.labelled(repo, LABEL) == {"wpc-1.1": "open"}


# --- the bound: no owned label write ------------------------------------------


@pytest.mark.usefixtures("no_br")
def test_adding_a_label_is_still_refused_so_this_read_has_no_engine_side_writer(
    tmp_path: Path,
) -> None:
    """The stated bound, pinned rather than described (basicly-wpc8).

    br *accumulates* labels while the ledger would record a replacement, so the seam
    refuses the flag rather than diverging the two stores. Nothing in the engine can
    therefore label a lane into a cut: a label this query finds was applied before the
    dual write, or carried by a ``create``. A future writer that lands must delete this
    test, which is the point of having it.
    """
    repo = owned_repo(tmp_path)

    with pytest.raises(owned_store.TrackerDivergenceError, match="--add-label"):
        br.write(repo, ["update", "wpc-1.1", "--add-label", LABEL])


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
