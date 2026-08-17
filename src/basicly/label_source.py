"""Which store answers *which records carry this label* (basicly-wpc8).

A pass's lane set is a label, not a parent-child edge, so this is the query that decouples
what a pass runs from what a bead's parent is (`supervise.lane_selection`). The boundary is
*a label's members* against :mod:`basicly.dependency_graph`, the blocking graph, and
:mod:`basicly.gate_source`, one record's gates; either store answers ``{issue_id: status}``.

**The matching write has no owned equivalent.** ``br update --add-label`` is refused at the
seam rather than mirrored, because br accumulates labels while the ledger would record a
replacement (`br_argv.UPDATE_FIELD_FLAGS`). So a label this reads was applied before the
dual write or carried by a ``create``; nothing else in the engine can add one, and that is
the bound on `loop supervise --label`.
"""

# comment-density-waiver: 5 documented members over 466 tokens of code, so the share is set
# by the member count and not by narration — the same shape as `gate_source` beside it. Two
# cutting passes took it 61.4% -> 57.0%; what is left is the two stores' closed-record rule
# and the refused label write, which is the one fact a caller cannot get from the code.

from __future__ import annotations

import json
from pathlib import Path

from basicly import br, owned_store

# The folded field a record's labels live under, on both sides: `mirror._FIELD_TYPES`
# stores br's comma-joined argv value as this list, so a reader never splits a string.
LABELS_FIELD = "labels"


def owned_labelled(repo_root: Path, label: str) -> dict[str, str]:
    """``{issue_id: status}`` for the ledger's records carrying *label*, closed included.

    Closed records are in, which is why the external half needs two spawns: a selection
    whose every bead has closed is a *finished* pass, and an empty one reports a completed
    cut as blocked. A tombstoned record is out, per :func:`br.owned_record`.

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
        if not state.tombstoned and label in _labels(state.fields.get(LABELS_FIELD))
    }


def _labels(value: object) -> tuple[str, ...]:
    """A folded ``labels`` field as the labels it names.

    A bare string is one label, never iterated: the mirror types the field as a list so it
    should not be one, and iterating it would match a single character instead.
    """
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list | tuple):
        return tuple(str(item) for item in value)
    return ()


def external_labelled(repo_root: Path, label: str) -> dict[str, str]:
    """``{issue_id: status}`` for the records ``br list --label`` reports for *label*.

    Two spawns, because br's default query omits ``closed`` and a ``--status`` allowlist
    would drop the statuses a project defined for itself.

    Raises:
        RuntimeError: br could not be run, or its reply was not usable JSON.
    """
    found = _listed(repo_root, ["list", "--label", label, "--json"])
    found |= _listed(repo_root, ["list", "--label", label, "--status", "closed", "--json"])
    return found


def _listed(repo_root: Path, args: list[str]) -> dict[str, str]:
    """One ``br list`` query as ``{issue_id: status}``, whatever payload shape it uses."""
    proc = br.run_br(repo_root, args)
    try:
        payload = json.loads(proc.stdout)
    except ValueError as exc:
        raise RuntimeError(f"br {' '.join(args)} returned no usable JSON: {exc}") from exc
    issues = payload.get("issues") if isinstance(payload, dict) else payload
    return {
        str(record["id"]): str(record.get("status", ""))
        for record in issues or ()
        if isinstance(record, dict) and "id" in record
    }


def labelled(repo_root: Path, label: str) -> dict[str, str]:
    """``{issue_id: status}`` for *label*, from whichever store the declared rung names."""
    if owned_store.tracker_mode(repo_root) == owned_store.MODE_OWNED:
        return owned_labelled(repo_root, label)
    return external_labelled(repo_root, label)
