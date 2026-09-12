from __future__ import annotations

from pathlib import Path

from basicly import owned_store, tracker_argv

LABELS_FIELD = tracker_argv.LABELS_FIELD


def owned_labelled(repo_root: Path, label: str) -> dict[str, str]:

    kit_module = owned_store.kit(repo_root)
    found = kit_module.read_ledger(owned_store.ledger_dir(repo_root))
    records = kit_module.events.fold(found).records
    return {
        record: state.status or ""
        for record, state in sorted(records.items())
        if not state.tombstoned and label in tracker_argv.labels_of(state.fields.get(LABELS_FIELD))
    }


def labelled(repo_root: Path, label: str) -> dict[str, str]:
    return owned_labelled(repo_root, label)
