from __future__ import annotations

from typing import TYPE_CHECKING, Any

from . import owned_store, redact
from .schema import ValidationError

_REFUSALS_SHOWN = 5

if TYPE_CHECKING:
    from pathlib import Path


def _lines(report: Any, *, source: str) -> list[str]:
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


def _source_prefix(record: str, valid: Any) -> str | None:

    parts = record.split("-")
    for cut in range(2, len(parts)):
        if valid("".join(parts[:cut]) + "-" + "-".join(parts[cut:])):
            return "-".join(parts[:cut])
    return None


def _refusal_lines(bad: list[Any], valid: Any) -> list[str]:

    if not bad:
        return []
    causes: dict[str, list[str]] = {}
    for record in bad:
        if not isinstance(record, str):
            cause = f"the id is not a string but a {type(record).__name__}"
        elif (prefix := _source_prefix(record, valid)) is not None:
            cause = (
                f"a record id is <prefix>-<suffix> and the prefix may not carry a hyphen, "
                f"so these read as prefix {prefix.split('-')[0]!r} with a hyphen left in the "
                f"suffix. The source prefix is {prefix!r}: a hyphenated one is not importable, "
                f"and the ids cannot be preserved under it"
            )
        else:
            cause = (
                "the id does not match <prefix>-<suffix> with lowercase letters and digits, "
                "an optional dotted child index, and no other punctuation"
            )
        causes.setdefault(cause, []).append(str(record))
    lines = []
    for cause, ids in causes.items():
        shown = ", ".join(repr(record) for record in sorted(ids)[:_REFUSALS_SHOWN])
        more = f", and {len(ids) - _REFUSALS_SHOWN} more" if len(ids) > _REFUSALS_SHOWN else ""
        lines.append(f"  refused, {len(ids)}: {cause}")
        lines.append(f"    {shown}{more}")
    return lines


def _prefix_note(repo_root: Path, records: list[str]) -> list[str]:

    if not records or owned_store.tracker_prefix(repo_root):
        return []
    prefixes = {record.split("-", 1)[0] for record in records if "-" in record}
    if len(prefixes) != 1:
        return []
    prefix = prefixes.pop()
    return [
        f"  this repository declares no [tracker] prefix and these ids use '{prefix}': add "
        f'prefix = "{prefix}" under [tracker] in basicly.toml before retiring the source '
        f"tracker, or no new root record can be minted in this namespace"
    ]


def preview(repo_root: Path, snapshot: Any, ledger: Path, kit: Any) -> tuple[int, list[str]]:

    held = {event.record for event in kit.events.read_events(ledger)[0] if hasattr(event, "record")}
    valid = kit.migrate.ids.is_record_id
    fresh, known, bad = [], [], []
    for raw in snapshot.records:
        record = raw.get("id")
        if not isinstance(record, str) or not valid(record):
            bad.append(record)
        elif record in held:
            known.append(record)
        else:
            fresh.append(record)
    lines = [
        f"import {snapshot.name} (dry run, nothing written): {len(fresh)} new record(s), "
        f"{len(known)} already in the ledger, {len(bad)} that would be refused"
    ]
    for label, ids in (("new", fresh), ("held", known)):
        if ids:
            lines.append(f"  {label}: {', '.join(sorted(ids))}")
    lines.extend(_refusal_lines(bad, valid))
    lines.extend(_prefix_note(repo_root, fresh))
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

    kit = owned_store.kit(repo_root)
    ledger = owned_store.ledger_dir(repo_root)
    try:
        snapshot = kit.migrate.read_snapshot(export, name=source_name)
    except (OSError, kit.migrate.SnapshotError) as exc:
        raise ValidationError(str(exc), export) from exc

    if dry_run:
        return preview(repo_root, snapshot, ledger, kit)

    report = kit.migrate.import_snapshot(
        ledger, snapshot, deleted=deleted, redact=redact.redact_committed
    )
    lines = _lines(report, source=snapshot.name)
    lines.extend(_prefix_note(repo_root, report.imported))
    return (1 if report.rejected or report.unreadable else 0), lines
