"""The owned tracker's cutover, reported to a human — the four `tracker` verbs.

Split out of `cli` when wiring `tracker adopt` left that module two tokens of headroom.
The boundary is one responsibility: every command here answers where the migration from
the external tracker to the owned ledger stands, or moves it one step. Parsing and
dispatch stay in `cli`. `Path.cwd()` rather than a passed root, following
`surface_report`: a cutover command is always run from the checkout it is about.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import br, loop_state, policy, ui
from .config import CHECKPOINTS, ENGINE_GATE_PROVIDERS, load_policy_config

if TYPE_CHECKING:
    import argparse

# Printed beside the two verdicts: `clean` is narrower than the sentence a reader takes from
# it (basicly-vkh0.41) — nine records reached the ledger with a status, comments, edges and
# gate rows, no `created` event, and `clean: yes` over all nine.
NOT_COMPARED = (
    "not compared: a record's title, description, type, priority or acceptance criteria — "
    "the three queries read its status, comments, edges and gate rows only, so a clean "
    "verdict does not say the owned ledger can reproduce a record"
)


def _differential_vocabulary(repo_root: Path) -> dict[str, Any]:
    """The engine's own names for the things the differential's three queries read.

    The kit's defaults mirror these constants and name each one, so passing them is
    not ceremony: the run that licenses a rung of the cutover has to compare on the
    gates this repo *configured*, and a repo that set ``[policy] required_gates``
    would otherwise be measured against the kit's default of ``verify`` alone.

    A plain mapping, because the seam takes one — this module never reaches into the
    kit, which ``test_no_module_outside_the_seam_reads_the_owned_store`` holds it to.

    Three of the kit's fields are left at its defaults, because the engine has no
    constant to pass: ``closed_statuses``, ``blocking_types`` (`merge` spells
    ``blocks`` inline) and ``parent_child_type``.
    """
    return {
        "marker": policy.MARKER,
        "checkpoints": tuple(CHECKPOINTS),
        "required_gates": tuple(load_policy_config(repo_root).required_gates),
        "engine_gate_providers": frozenset(ENGINE_GATE_PROVIDERS),
        "worktree_ref_prefix": loop_state.WORKTREE_REF_PREFIX,
        "known_statuses": frozenset(loop_state.KNOWN_STATUSES),
        "dispatchable_statuses": frozenset(loop_state.DISPATCHABLE_STATUSES),
    }


def cmd_shadow(args: argparse.Namespace) -> int:
    """Run the shadow differential and report ``clean`` and ``conclusive`` separately.

    Step 2 of the cutover (`docs/requirements/work-tracker.md` §5). The two verdicts are
    printed as two lines and the exit code needs both, because a single answer would
    let the weaker question stand in for the stronger one: a comparison where every
    record gave one query the same answer agreed about nothing, and reporting that as
    a pass is the failure mode this whole design keeps paying for.

    A refused reference is reported in the same breath as the agreement it voids —
    ``summary()`` carries the refusal — so a run that proves nothing cannot read as a
    run that proved something, and :data:`NOT_COMPARED` says what a clean one skipped.

    The run is judged on records created after the flip (basicly-c357). ``--declare-history``
    records today's delta as that boundary and writes nothing else; it is a one-time
    declaration made at the flip, so it prints the count it captured rather than a verdict.
    """
    repo_root = Path.cwd()
    vocabulary = _differential_vocabulary(repo_root)
    if args.declare_history:
        stamp = datetime.now(UTC).date().isoformat()
        declared = br.declare_differential_baseline(repo_root, stamp, vocabulary)
        ui.say(f"declared {len(declared.records)} historical record(s) on {stamp}", style="ok")
        return 0
    report = br.scoped_differential(repo_root, vocabulary)
    ui.say(report.summary())
    ui.say(f"clean:      {'yes' if report.clean else 'no'}")
    ui.say(f"conclusive: {'yes' if report.conclusive else 'no'}")
    ui.say(NOT_COMPARED)
    if report.clean and report.conclusive:
        ui.say(
            "The owned ledger agrees with the live tracker, and the agreement means something.",
            style="ok",
        )
        return 0
    ui.say("The next rung of the cutover is not licensed by this run.", style="warn")
    return 1


def cmd_import(args: argparse.Namespace) -> int:
    """Bring the owned ledger up to the committed export (§5 step 1).

    The entry point the import never had (`basicly-vkh0.23`): it was run once by hand in a
    python one-liner, so the ledger drifted 24 records behind within a day and nothing a
    fresh consumer runs could build one at all.

    ``--dry-run`` reports the same refusal the write would, rather than only the counts: a
    preview that says "would add 200" for a run that will refuse is worse than no preview.
    """
    repo_root = Path.cwd()
    preview = br.import_preview(repo_root)
    ui.say(f"ledger {preview.ledger} records, export {preview.export}")
    if preview.native:
        ui.say(
            f"refused: the ledger holds {len(preview.native)} record(s) created after the "
            "flip, so re-importing would close the gap the differential is judged against",
            style="warn",
        )
        return 1
    if args.dry_run:
        ui.say(f"would add {len(preview.adds)} records, 0 tombstones")
        return 0
    # No actor: `basicly-r166` is open on the ledger committing a username in every event,
    # so this entry point does not add a second producer of the same defect.
    report = br.import_export(repo_root)
    ui.say(f"added {len(report.imported)} records, {len(report.tombstoned)} tombstones")
    for record in report.diverged:
        ui.say(f"  diverged, left as it stands: {record}")
    return 0


def cmd_write(args: argparse.Namespace) -> int:
    """Run one hand-authored tracker write through ``br.write``.

    Spawning ``br`` directly never enters the seam, so a hand-run write moves one store and
    not the other: three records arrived that way and are the whole of what still fails the
    differential (basicly-vkh0.24).
    """
    argv = [arg for arg in (args.argv or []) if arg != "--"]
    if not argv:
        ui.say("tracker write: name a br subcommand, e.g. `-- close b-1`")
        return 2
    # The one write whose output the caller needs: the id the store minted (vkh0.29).
    if argv[0] == "create":
        ui.say(f"created: {br.create_record(Path.cwd(), argv)}")
        return 0
    br.write(Path.cwd(), argv)
    ui.say(f"recorded: {' '.join(argv)}")
    return 0


def cmd_adopt(_args: argparse.Namespace) -> int:
    """Reconcile what a hand-run br created into the ledger (basicly-vkh0.24, .32).

    The repair for the write `cmd_write` exists to prevent. Re-runnable, so a
    later bypass is repaired by running it again rather than by a one-off.

    Every reconciled edge is named rather than counted: it is agreement the repair
    manufactured, on a record the differential still judges as dual-written.
    """
    report = br.adopt_hand_writes(Path.cwd())
    ui.say(f"adopted {len(report.adopted)} record(s): {', '.join(report.adopted) or 'none'}")
    for record, target, edge_type in report.edges:
        ui.say(f"  adopted edge {record} -> {target} ({edge_type})")
    for record in report.diverged:
        ui.say(f"  {record} has a hand-edited field the ledger keeps as first written")
    for record in report.unadoptable:
        ui.say(
            f"  {record} is in br and not in the export, as a record or an edge: re-export first",
            style="warn",
        )
    return 1 if report.unadoptable else 0
