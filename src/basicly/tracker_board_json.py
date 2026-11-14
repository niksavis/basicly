from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from basicly import owned_store


def _capture(repo_root: Path) -> tuple[Any, Any, Path, list[dict], list[dict], str]:
    context = owned_store.vault_context(repo_root)
    if context is None:
        owned_store.packaged_kit("vault_cli").configured(repo_root, "--all-projects")
        raise ValueError("--all-projects requires a configured vault")
    view, clone, config = context
    ledger = owned_store.ledger_dir(repo_root)
    want = view.Want(config.prefix, True, ledger if ledger.is_dir() else None)
    view.present(clone, want, clock=time.time)
    queue = owned_store.packaged_kit("vault_queue")
    try:
        with (
            view.events.LedgerLock(clone.folder / "fetch"),
            view.events.LedgerLock(clone.folder),
        ):
            published = view.vault_clone.valid_events(clone)
            pending = queue.pending(clone.folder / queue.QUEUE_NAME)
            head = (clone.folder / view.vault_clone.COVERS_NAME).read_text().strip()
    except OSError, view.events.LedgerError:
        raise ValueError(
            "the vault view could not be captured; run tracker status and retry"
        ) from None
    if len(head) not in (40, 64) or any(char not in "0123456789abcdef" for char in head):
        raise ValueError("the vault view has no valid head; run tracker status and retry")
    return view, config, ledger, published, pending, head


def _ready(view: Any, found: list, published: list[dict], ledger: Path, prefix: str) -> set[str]:
    scheduler = owned_store.packaged_kit("scheduler")
    projects = {event.record.partition("-")[0] for event in found}
    candidates = {}
    for project in sorted(projects):
        template = view.vault_template.carried(published, project)
        if template is None:
            template = view.templates.read(ledger) if project == prefix and ledger.is_dir() else {}
        parsed = view.templates.parse(template, ledger / view.templates.TEMPLATE_FILE)
        candidates.update({
            record: candidate
            for record, candidate in scheduler.candidates_from_events(found, parsed).items()
            if record.partition("-")[0] == project
        })
    return {row.record for row in scheduler.rank(candidates).records}


def report(
    repo_root: Path, *, status: list[str] | None = None, limit: int | None = None, query: str = ""
) -> dict[str, Any]:
    if limit is not None and limit < 0:
        raise ValueError("--limit must not be negative")
    values = owned_store.packaged_kit("values")
    for value in status or ():
        values.require_status(value)
    view, config, ledger, published, pending, head = _capture(repo_root)
    want = view.Want(config.prefix, True)
    projection = owned_store.packaged_kit("vault_pending")
    combined, held, unshared = projection.projection(published, pending, want)
    found = view.renumbered(view.carried(combined, want), held)
    states = view.events.fold(found).records
    ready = _ready(view, found, published, ledger, config.prefix)
    canonical = view.vault_fold.fold(published).records
    records = []
    needle = query.casefold()
    for record, state in sorted(states.items()):
        if state.tombstoned or (status is not None and state.status not in status):
            continue
        searchable = [record, state.status, *state.fields.values(), *state.comments]
        if needle and needle not in json.dumps(searchable, ensure_ascii=False).casefold():
            continue
        owner = canonical.get(record)
        holder = owner.holder if owner is not None else None
        records.append({
            "id": record,
            "title": str(state.fields.get("title") or ""),
            "status": state.status,
            "holder": {"writer": holder[0], "session": holder[1]} if holder else None,
            "priority": state.fields.get("priority", 2),
            "project": record.partition("-")[0],
            "ready": record in ready,
            **({"sharing": "pending"} if record in unshared else {}),
        })
    return {
        "read_at": datetime.now(UTC).isoformat(),
        "vault_head": head,
        "records": records if limit is None else records[:limit],
    }
