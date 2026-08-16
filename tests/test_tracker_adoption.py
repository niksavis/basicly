"""The repair for a tracker write made outside the engine seam (basicly-vkh0.24).

A record created by running `br` **by hand** while `[tracker] mode = "dual"` lands on the
external store only: `br._mirror_write` sits in the engine seam and a spawned binary never
enters it. Neither existing route repairs that — `tracker import` refuses once the ledger
holds a post-flip record, `--declare-history` refuses a second declaration — and both
refusals are deliberate, so the demonstration below asserts the refusal *before* it asserts
the repair. Without that half, "the repair works" would not say the repair was needed.

The stand-in br here is a **read-only reference**: `adopt_hand_writes` writes to the ledger
and never to br, so the three reads the shadow differential makes are the whole surface it
needs. `tests/test_br_seam.py` owns the write side and its own richer stand-in; a second
copy of that class here would be 200 lines to exercise one `create`.

Nothing here spawns a process or reads the host's tracker.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from basicly import br
from basicly.config import load_tracker_mode

REPO_ROOT = Path(__file__).resolve().parent.parent
KIT_SOURCE = REPO_ROOT / ".basicly" / "core" / "kit" / "tracker"


class _ReferenceBr:
    """A br that answers the differential's three reads out of its own record store.

    Independent of the owned ledger by construction, which is what
    `differential.audit_reference`'s perturbation probe checks: its answers cannot move
    when a synthetic event is appended to a ledger it has never read.
    """

    def __init__(self, records: dict[str, dict[str, Any]]) -> None:
        self.records = records

    def __call__(self, cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        args = list(cmd[1:])
        if args[:1] == ["--version"]:
            return _proc(f"br {br.PINNED_VERSION}")
        if args[:1] == ["list"]:
            return _proc(json.dumps({"issues": list(self.records.values())}))
        if args[:1] == ["show"]:
            named = [arg for arg in args[1:] if not arg.startswith("-")]
            return _proc(json.dumps([self.records[record] for record in named]))
        if args[:2] == ["gate", "list"]:
            return _proc(json.dumps({"issue_id": args[2], "results": []}))
        return _proc("", stderr=f"Error: unknown command {' '.join(args)}", returncode=2)


def _proc(stdout: str, *, stderr: str = "", returncode: int = 0):
    return subprocess.CompletedProcess(["br"], returncode, stdout, stderr)


def _record(issue: str, **fields: Any) -> dict[str, Any]:
    """One record in the shape both `br show --json` and the JSONL export use."""
    return {"id": issue, "status": "open", "comments": [], "dependencies": [], **fields}


def _blocks(issue: str, target: str) -> dict[str, Any]:
    """One blocking edge in the export's spelling, which `br dep add` echoes back."""
    return {"issue_id": issue, "depends_on_id": target, "type": "blocks"}


def _repo(tmp_path: Path, records: dict[str, dict[str, Any]]) -> Path:
    """A dual-mode checkout whose committed export holds *records*."""
    (tmp_path / br.KIT_TRACKER_DIR).mkdir(parents=True)
    for source in sorted(KIT_SOURCE.glob("*.py")):
        shutil.copy2(source, tmp_path / br.KIT_TRACKER_DIR / source.name)
    (tmp_path / br.LEDGER_DIR).mkdir(parents=True)
    (tmp_path / ".beads").mkdir()
    _write_export(tmp_path, records)
    (tmp_path / "basicly.toml").write_text(
        f'[tracker]\nmode = "{br.MODE_DUAL}"\n', encoding="utf-8"
    )
    # Importing `basicly.config` is what installs the mode reader `br` refuses to spawn
    # without, so the repo's own answer is read back rather than assumed.
    assert load_tracker_mode(tmp_path) == br.MODE_DUAL
    return tmp_path


def _write_export(repo: Path, records: dict[str, dict[str, Any]]) -> None:
    (repo / ".beads" / br.EXPORT_NAME).write_text(
        "".join(json.dumps(record) + "\n" for record in records.values()), encoding="utf-8"
    )


def _origin(repo: Path, record: str) -> str:
    """The import label on *record*'s created event — how the ledger says it got here."""
    events = br.kit(repo).read_ledger(br.ledger_dir(repo))
    return br.kit(repo, br.BASELINE_MODULE).origins(events)[record]


@pytest.fixture
def hand_written(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, _ReferenceBr]:
    """A repo in the state the flip left this one in, one hand write and all.

    ``imported`` arrived through the import at the flip, ``mirrored`` through the dual
    write, and ``byhand`` by somebody running `br create` in a terminal — so it is in br
    and in the export br rewrote, and in the ledger not at all.
    """
    records = {name: _record(name) for name in ("seam-imported", "seam-mirrored", "seam-byhand")}
    reference = _ReferenceBr(records)
    monkeypatch.setattr(br, "which", lambda: "/usr/bin/br")
    monkeypatch.setattr(br, "_probed_paths", {"/usr/bin/br"})
    monkeypatch.setattr(br.subprocess, "run", reference)

    repo = _repo(tmp_path, {"seam-imported": records["seam-imported"]})
    br.import_export(repo)
    _write_export(repo, records)
    kit = br.kit(repo)
    kit.events.append(
        br.ledger_dir(repo),
        [
            kit.events.Draft(
                "seam-mirrored", kit.events.KIND_CREATED, {"provenance": "dual-write"}
            ),
            kit.events.Draft(
                "seam-mirrored",
                kit.events.KIND_STATUS,
                {"provenance": "dual-write", "status": "open"},
            ),
        ],
    )
    return repo, reference


def test_a_hand_write_is_a_finding_no_existing_route_can_repair(
    hand_written: tuple[Path, _ReferenceBr],
) -> None:
    """The defect, and the two closed doors — asserted before the repair, not after."""
    repo, _ = hand_written

    scoped = br.scoped_differential(repo)
    assert scoped.undeclared == ("seam-byhand",)
    assert not scoped.clean

    with pytest.raises(br.TrackerDivergenceError, match="after the flip"):
        br.import_export(repo)
    br.declare_differential_baseline(repo, "2026-08-16")
    boundary = br.kit(repo, br.BASELINE_MODULE)
    with pytest.raises(boundary.BaselineError, match="already declared"):
        br.declare_differential_baseline(repo, "2026-08-16")


def test_a_hand_write_reaches_the_ledger_marked_as_adopted(
    hand_written: tuple[Path, _ReferenceBr],
) -> None:
    """The bead's acceptance: it reaches the owned ledger, and it carries the marker.

    Both halves, because either alone is a way to report a green run over the defect. The
    record has to be *there* — a marker on an absent record is the "declare the divergence
    acceptable" option — and it has to be *marked*, or the repair's own agreement would be
    counted as evidence that the dual write works.
    """
    repo, _ = hand_written

    report = br.adopt_hand_writes(repo)

    assert report.adopted == ("seam-byhand",)
    assert report.unadoptable == ()
    assert _origin(repo, "seam-byhand") == br.kit(repo, br.BASELINE_MODULE).ADOPTION_SOURCE
    assert _origin(repo, "seam-imported") == br.IMPORT_SOURCE

    scoped = br.scoped_differential(repo)
    assert scoped.clean and scoped.conclusive
    assert scoped.adopted == ("seam-byhand",)
    assert set(scoped.in_scope) == {"seam-byhand", "seam-mirrored"}


def test_a_hand_write_the_export_missed_is_reported_and_not_invented(
    hand_written: tuple[Path, _ReferenceBr],
) -> None:
    """A record br holds that the committed export does not cannot be adopted from it.

    The export is what a hand `br create` updates on its next sync, so a stale one is the
    likely failure. Reading the *live* tracker instead would repair it — and would also
    make the later differential agree with itself, because the ledger would then be a copy
    of the very side it is compared against.
    """
    repo, reference = hand_written
    _write_export(
        repo, {name: reference.records[name] for name in ("seam-imported", "seam-mirrored")}
    )

    report = br.adopt_hand_writes(repo)

    assert report.adopted == ()
    assert report.unadoptable == ("seam-byhand",)
    assert br.scoped_differential(repo).undeclared == ("seam-byhand",)


def test_the_repair_is_re_runnable_rather_than_a_one_shot(
    hand_written: tuple[Path, _ReferenceBr],
) -> None:
    """basicly-vkh0.23's defect, not repeated: a second hand write is repaired the same way.

    The second run re-reads every already-adopted record, so it is the same command rather
    than a widening one-off, and a third run over an unchanged tree appends nothing.
    """
    repo, reference = hand_written
    br.adopt_hand_writes(repo)

    reference.records["seam-later"] = _record("seam-later", status="closed")
    _write_export(repo, reference.records)
    second = br.adopt_hand_writes(repo)

    assert second.adopted == ("seam-later",)
    assert br.scoped_differential(repo).adopted == ("seam-byhand", "seam-later")

    settled = len(br.kit(repo).read_ledger(br.ledger_dir(repo)))
    third = br.adopt_hand_writes(repo)
    assert third.adopted == ()
    assert len(br.kit(repo).read_ledger(br.ledger_dir(repo))) == settled


def test_a_hand_edited_field_on_an_adopted_record_is_reported_not_rewritten(
    hand_written: tuple[Path, _ReferenceBr],
) -> None:
    """The one hand write a re-run cannot reconcile, said out loud rather than absorbed.

    A second ``created`` event would fold over the first and make the import a sync
    (§5.1), so `import_snapshot` reports the record as diverged and writes nothing. A run
    that printed "adopted 0" and stopped there would leave the operator reading a clean
    line over an unrepaired edit.
    """
    repo, reference = hand_written
    br.adopt_hand_writes(repo)

    reference.records["seam-byhand"]["title"] = "retitled by hand"
    _write_export(repo, reference.records)
    again = br.adopt_hand_writes(repo)

    assert again.diverged == ("seam-byhand",)
    assert again.adopted == ()


def test_an_edge_the_ledger_never_saw_is_adopted_onto_a_record_it_already_holds(
    hand_written: tuple[Path, _ReferenceBr],
) -> None:
    """basicly-vkh0.32: the repair stopped at the records and left the edges between them.

    A hand-run `br dep add` on a record both stores already hold is the one bypass no
    seam-routed write can undo — br rejects the duplicate, so `_mirror_write` never fires
    and the ledger stays short of the edge permanently. The differential sees it as the
    ready query: the owned side has no blocker to be blocked by.
    """
    repo, reference = hand_written
    reference.records["seam-mirrored"]["dependencies"] = [_blocks("seam-mirrored", "seam-imported")]
    _write_export(repo, reference.records)
    assert [item.query for item in br.scoped_differential(repo).disagreements] == ["ready"]

    report = br.adopt_hand_writes(repo)

    assert report.edges == (("seam-mirrored", "seam-imported", "blocks"),)
    assert br.scoped_differential(repo).clean


def test_an_edge_both_stores_already_hold_is_not_appended_a_second_time(
    hand_written: tuple[Path, _ReferenceBr],
) -> None:
    """The re-run half, and the reason the repair is safe to keep running.

    An edge that agrees is a fact the ledger holds, so a second run has nothing to add. A
    repair that appended one anyway would grow the ledger on every invocation and record
    the same dependency twice under two provenances.
    """
    repo, reference = hand_written
    reference.records["seam-mirrored"]["dependencies"] = [_blocks("seam-mirrored", "seam-imported")]
    _write_export(repo, reference.records)
    br.adopt_hand_writes(repo)

    settled = len(br.kit(repo).read_ledger(br.ledger_dir(repo)))
    again = br.adopt_hand_writes(repo)

    assert again.edges == ()
    assert len(br.kit(repo).read_ledger(br.ledger_dir(repo))) == settled


def test_an_edge_the_export_missed_is_reported_and_not_copied_from_the_reference(
    hand_written: tuple[Path, _ReferenceBr],
) -> None:
    """The edge half of the rule that keeps the later differential worth running.

    An edge taken from the side the differential compares against would agree by
    construction. Taken from the committed export, a stale export stays visible as the
    disagreement it is — so the run says which record needs re-exporting and writes nothing.
    """
    repo, reference = hand_written
    reference.records["seam-mirrored"]["dependencies"] = [_blocks("seam-mirrored", "seam-imported")]

    report = br.adopt_hand_writes(repo)

    assert report.edges == ()
    assert report.unadoptable == ("seam-mirrored",)
    assert [item.query for item in br.scoped_differential(repo).disagreements] == ["ready"]
