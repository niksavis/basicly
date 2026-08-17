"""One write the engine makes itself, appended to the owned ledger with no process spawned.

The flipped half of the write path (basicly-wpc8): :mod:`basicly.mirror` translates a write
the external tracker has *already* accepted, and everything here records one no external
tracker saw. Both go through ``mirror.drafts``, so what a write means cannot depend on which
store took it.

The boundary is *the append* against :mod:`basicly.br`, which decides which store a write
reaches and refuses one inside a read-only section.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from basicly import br_argv, mirror, owned_store, redact
from basicly.owned_store import TrackerDivergenceError

# How a write the engine made itself says it got here — as against one the dual write
# mirrored (:data:`mirror.MIRROR_PROVENANCE`) and one `migrate.py` extracted out of the
# export. One of `migrate.RESERVED_KEYS`, so it is dropped when a record is rendered back.
OWNED_PROVENANCE = "engine"


def _stamped(kit_module: Any, drafts: Sequence[Any]) -> list[Any]:
    """*drafts* restamped as the engine's own write rather than a mirrored one.

    ``replace``, not a fresh draft: a field the translator sets and this function does not
    know about has to survive the way through.
    """
    return [
        replace(
            draft,
            payload={**draft.payload, kit_module.migrate.PROVENANCE_KEY: OWNED_PROVENANCE},
        )
        for draft in drafts
    ]


def append(repo_root: Path, args: Sequence[str]) -> None:
    """Record on the owned ledger the fact *args* states.

    The echo is empty because no process ran, which is why ``create`` cannot come through
    here — its translation reads the reply for the minted id, and :func:`create` is that
    surface.

    Raises:
        TrackerDivergenceError: the kit is not installed, the write has no owned-ledger
            translation, or the append failed.
    """
    kit_module = owned_store.kit(repo_root)
    try:
        drafts = mirror.drafts(kit_module, args, "")
        kit_module.events.append(
            owned_store.ledger_dir(repo_root),
            _stamped(kit_module, drafts),
            redact=redact.redact_committed,
        )
    except (kit_module.events.LedgerError, OSError, ValueError) as exc:
        raise TrackerDivergenceError(
            f"br {' '.join(args)} did not reach the owned ledger: {exc}"
        ) from exc


def create(repo_root: Path, args: Sequence[str]) -> str:
    """Mint the id one ``br create`` *args* asks for and record the create; return the id.

    **The mint and the append are one critical section**, over the ledger's own lock, the
    rule the kit's ``cli.create_record`` states: minting reads every id the ledger ever held
    and `supervise` runs its lanes concurrently, so a writer appending in between could be
    handed the same id. Only the ``--parent`` form has an owned equivalent — a root mint
    needs a prefix that lives in the external tracker's config — and every create the engine
    makes names one (`decompose._create_child`).

    Raises:
        TrackerDivergenceError: the create names no parent, the kit is not installed, or
            the ledger refused the write.
    """
    kit_module = owned_store.kit(repo_root)
    events = kit_module.events
    parent = dict(br_argv.flag_pairs(args, br_argv.VALUE_FLAGS["create"])).get("--parent", "")
    if not parent:
        raise TrackerDivergenceError(
            f"br create with no --parent has no owned equivalent: the id prefix a root "
            f"mint needs is the external tracker's own, so {' '.join(args)} would have to "
            f"guess it; name a parent"
        )
    ledger = owned_store.ledger_dir(repo_root)
    try:
        with events.LedgerLock(ledger) as lock:
            # Every id the ledger ever held, tombstones included: `ids.minted_ever`'s rule
            # is that a deleted record's id is never handed out again.
            minted = set(events.fold(events.read_events(ledger)[0]).records)
            record = events.ids.next_child_id(parent, minted)
            drafts = mirror.drafts(kit_module, args, json.dumps({"id": record}))
            events.append(
                ledger,
                _stamped(kit_module, drafts),
                redact=redact.redact_committed,
                held_lock=lock,
            )
    except (events.LedgerError, OSError, ValueError) as exc:
        raise TrackerDivergenceError(
            f"br {' '.join(args)} did not reach the owned ledger: {exc}"
        ) from exc
    return record
