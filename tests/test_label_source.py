from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from basicly import label_source, owned_store, supervise, tracker
from tests.test_owned_write import no_br, owned_repo

__all__ = ["no_br"]

LABEL = "phase-6"


def _proc(stdout: str) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["br"], 0, stdout, "")


def _labelled(repo: Path, records: dict[str, tuple[str, object]]) -> None:

    kit = owned_store.kit(repo)
    drafts = []
    for record, (status, labels) in records.items():
        fields = {} if labels is None else {label_source.LABELS_FIELD: labels}
        drafts.append(kit.events.Draft(record, kit.events.KIND_CREATED, fields))
        drafts.append(kit.events.Draft(record, kit.events.KIND_STATUS, {"status": status}))
    kit.events.append(owned_store.ledger_dir(repo), drafts)


@pytest.mark.usefixtures("no_br")
def test_the_read_selects_the_labelled_records_closed_ones_included(
    tmp_path: Path,
) -> None:

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

    repo = owned_repo(tmp_path)
    _labelled(repo, {"wpc-1.1": ("open", LABEL), "wpc-1.2": ("open", "p")})

    assert label_source.labelled(repo, LABEL) == {"wpc-1.1": "open"}
    assert label_source.labelled(repo, "p") == {"wpc-1.2": "open"}


def test_the_read_answers_from_the_fold_with_nothing_spawned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    repo = owned_repo(tmp_path)
    _labelled(repo, {"wpc-1.1": ("open", [LABEL])})
    monkeypatch.setattr(
        subprocess, "run", lambda *_a, **_k: pytest.fail("the read spawned a process")
    )

    assert label_source.labelled(repo, LABEL) == {"wpc-1.1": "open"}


@pytest.mark.usefixtures("no_br")
def test_a_label_written_through_the_seam_is_found_by_this_read(tmp_path: Path) -> None:

    repo = owned_repo(tmp_path)
    _labelled(repo, {"wpc-1.1": ("open", [])})

    tracker.write(repo, ["update", "wpc-1.1", "--add-label", LABEL])

    assert label_source.labelled(repo, LABEL) == {"wpc-1.1": "open"}


@pytest.mark.usefixtures("no_br")
def test_a_mistyped_label_refuses_the_pass_rather_than_deriving_an_empty_session(
    tmp_path: Path,
) -> None:
    repo = owned_repo(tmp_path)
    _labelled(repo, {"wpc-1.1": ("open", [LABEL])})

    with pytest.raises(supervise.LaneSelectionError, match="carries label 'phase-7'"):
        supervise.lane_selection(repo, "phase-7")
    assert supervise.lane_selection(repo, LABEL) == (("wpc-1.1", "open"),)
