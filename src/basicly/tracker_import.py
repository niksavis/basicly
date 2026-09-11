"""Import a foreign tracker's export into the owned ledger (basicly-lc2bd3v).

The kit has carried :mod:`migrate` — a tested importer that preserves source ids, keeps
unknown fields verbatim, and re-runs as a replay — with **no** production caller and no
command. So the only supported migration was for a consumer to write Python against a
kit module, which is why two of them concluded the path was gone and planned to re-file
live work by hand. This is the seam that makes it reachable.

The import is deliberately not a sync (`.basicly/core/kit/tracker/SPEC.md` §5.1): a record
the ledger already holds is never re-created, a field disagreement is reported rather than
reconciled, and absence from the snapshot is never read as a deletion.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from . import owned_store, redact
from .schema import ValidationError

if TYPE_CHECKING:
    from pathlib import Path


def _lines(report: Any, *, source: str) -> list[str]:
    """One line per outcome the caller can act on, quietest case first."""
    lines = [
        f"import {source}: {len(report.imported)} record(s) created, "
        f"{len(report.events)} event(s) appended"
    ]
    for label, ids in (
        ("diverged (reported, not reconciled)", report.diverged),
        ("absent from this snapshot (NOT deleted)", report.absent),
        ("tombstoned", report.tombstoned),
    ):
        if ids:
            lines.append(f"  {label}: {', '.join(ids)}")
    for label, items in (("unreadable", report.unreadable), ("rejected", report.rejected)):
        lines.extend(f"  {label}: {item.subject} — {item.reason}" for item in items)
    return lines


def preview(snapshot: Any, ledger: Path, kit: Any) -> tuple[int, list[str]]:
    """What an import would do, having written nothing; the exit code the real run gives.

    Reads the ledger to separate records it already holds from new ones, so a consumer
    can see the id set before it is committed rather than after.

    The code matches :func:`run_import`'s rather than being 0 always (basicly-1yychkj):
    a dry run that reports a refusal and still exits 0 lets a scripted preflight pass
    and the real run then fail, which is the one thing the preflight exists to prevent.
    """
    held = {event.record for event in kit.events.read_events(ledger)[0] if hasattr(event, "record")}
    # The same id rule the write applies. A preview that skipped it would list an id the
    # import is about to refuse as an incoming record, which is the one thing a consumer
    # runs a dry run to find out before committing the ledger.
    valid = kit.migrate.ids.is_record_id
    fresh, known, bad = [], [], []
    for raw in snapshot.records:
        record = raw.get("id")
        if not isinstance(record, str) or not valid(record):
            bad.append(repr(record))
        elif record in held:
            known.append(record)
        else:
            fresh.append(record)
    lines = [
        f"import {snapshot.name} (dry run, nothing written): {len(fresh)} new record(s), "
        f"{len(known)} already in the ledger, {len(bad)} that would be refused"
    ]
    for label, ids in (("new", fresh), ("held", known), ("would be refused", bad)):
        if ids:
            lines.append(f"  {label}: {', '.join(sorted(ids))}")
    lines.extend(f"  unreadable: {i.subject} — {i.reason}" for i in snapshot.unreadable)
    return (1 if bad or snapshot.unreadable else 0), lines


def run_import(
    repo_root: Path,
    export: Path,
    *,
    source_name: str | None = None,
    dry_run: bool = False,
    deleted: tuple[str, ...] = (),
) -> tuple[int, list[str]]:
    """Import *export* into the repo's owned ledger; return an exit code and its report.

    Raises:
        ValidationError: the export cannot be read or is not a portable snapshot.
    """
    kit = owned_store.kit(repo_root)
    ledger = owned_store.ledger_dir(repo_root)
    try:
        snapshot = kit.migrate.read_snapshot(export, name=source_name)
    except (OSError, kit.migrate.SnapshotError) as exc:
        raise ValidationError(str(exc), export) from exc

    if dry_run:
        return preview(snapshot, ledger, kit)

    # The redactor every other engine write passes (`owned_write`). Without it the export's
    # own `source_repo_path` and `created_by` reach the committed ledger verbatim, and
    # `tracker-path-scan` then refuses the commit that would publish them (basicly-npiudkl).
    report = kit.migrate.import_snapshot(
        ledger, snapshot, deleted=deleted, redact=redact.redact_committed
    )
    lines = _lines(report, source=snapshot.name)
    # Rejections are the finding this importer exists to surface rather than swallow, so
    # they set the exit code; a divergence does not, because nothing was written wrongly.
    return (1 if report.rejected or report.unreadable else 0), lines
