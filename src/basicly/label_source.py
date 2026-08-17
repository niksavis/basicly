"""Which records carry a given label (basicly-wpc8).

A pass's lane set is a label, not a parent-child edge, so this is the query that decouples
what a pass runs from what a bead's parent is (`supervise.lane_selection`). The boundary is
*a label's members* against :mod:`basicly.dependency_graph`, the blocking graph, and
:mod:`basicly.gate_source`, one record's gates; the answer is ``{issue_id: status}``.

The matching write is :func:`basicly.owned_write.append`, which resolves ``--add-label``
and ``--remove-label`` against the record's current set under the ledger lock and appends
one replacement (`tracker_argv.UPDATE_LABEL_FLAGS`). So a label this reads was applied by that
write, carried by a ``create``, or extracted by the import.
"""

# comment-density-waiver: 3 documented members over 250 tokens of code, so the share is set
# by the member count and not by narration — the same shape as `gate_source` beside it.
# What is left is why a closed record stays in the answer, which is the one fact a caller
# cannot get from the code.

from __future__ import annotations

from pathlib import Path

from basicly import owned_store, tracker_argv

LABELS_FIELD = tracker_argv.LABELS_FIELD


def owned_labelled(repo_root: Path, label: str) -> dict[str, str]:
    """``{issue_id: status}`` for the ledger's records carrying *label*, closed included.

    Closed records are in: a selection whose every bead has closed is a *finished* pass,
    and an empty one reports a completed cut as blocked. A tombstoned record is out, per
    :func:`tracker.owned_record`.

    Raises:
        TrackerDivergenceError: the kit is not installed or will not load. A hard failure,
            because an empty answer is what `lane_selection` refuses a mistyped label on.
    """
    kit_module = owned_store.kit(repo_root)
    found = kit_module.read_ledger(owned_store.ledger_dir(repo_root))
    # Not named `folded`: `.scripts/wired_or_deleted.py` counts an identifier anywhere
    # outside `tests/` as a read of a same-named record field, so a local by that name
    # retires the suppression on `basicly.supervise.DispatchBundle.folded`.
    records = kit_module.events.fold(found).records
    return {
        record: state.status or ""
        for record, state in sorted(records.items())
        if not state.tombstoned and label in tracker_argv.labels_of(state.fields.get(LABELS_FIELD))
    }


def labelled(repo_root: Path, label: str) -> dict[str, str]:
    """``{issue_id: status}`` for *label*, folded out of the owned ledger."""
    return owned_labelled(repo_root, label)
