"""Which store answers *what gate results does this record carry* (basicly-vkh0.27).

The cutover rung decides: the owned ledger's fold, a ``br gate list`` spawn, or — on
``dual`` — both, with a disagreement refused. What the rows *mean* stays in
:mod:`basicly.policy`, this module's one engine caller.

The boundary is *the gate query* against :mod:`basicly.br`, which owns the record and
ranking seams and is the only module that spawns br. A module of its own because it must
import ``br`` and ``br`` may therefore not import it: `br._live_gate_rows`, the shadow
differential's reference side, has to spawn br on every rung and stays there.

Either store answers in br's own ``results`` shape, the way :func:`br.owned_record` renders
``br show``'s, so the caller has one parser.
"""

# comment-density-waiver: 5 documented members plus the module contract over 618 tokens of
# code, so the share is set by the member count. What is left after two cutting passes
# (67.0% -> 58.6%) is provenance: the export that cannot carry a gate field, the bypass the
# excusal cannot tell apart, and the deletion rule the two stores spell differently.

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from basicly import br, owned_store

# br's row keys, which are also the kit's payload keys (`gates.GATE_NAME_KEY` and its
# siblings): this module renders one shape into the other, so both vocabularies meet here.
GATE_KEY = "gate"
PROVIDER_KEY = "provider"
PASSED_KEY = "passed"


def owned_gates(repo_root: Path, issue_id: str) -> list[dict]:
    """*issue_id*'s gate rows as the owned ledger holds them, in br's row shape.

    Empty when no gate event named the record — `gates.GateFold.view`'s ordinary state, a
    gate nobody reported not being green. **A tombstoned record answers empty too**, the
    rule :func:`br.owned_record` gives: the two stores spell a deletion differently, and
    serving a deleted bead's verdicts would advance work somebody removed.

    Raises:
        TrackerDivergenceError: the kit is not installed or will not load. A hard failure,
            because an empty answer here reads as a gate that never ran.
    """
    kit_module = owned_store.kit(repo_root)
    gates = owned_store.kit(repo_root, owned_store.GATES_KIT_MODULE)
    found = kit_module.read_ledger(owned_store.ledger_dir(repo_root))
    state = kit_module.events.fold(found).records.get(issue_id)
    if state is not None and state.tombstoned:
        return []
    return [
        {GATE_KEY: result.gate, PROVIDER_KEY: result.provider, PASSED_KEY: result.passed}
        for result in gates.fold_gates(found).view(issue_id).results
    ]


def external_gates(repo_root: Path, issue_id: str) -> list[dict]:
    """*issue_id*'s gate rows as ``br gate list --robot`` reports them.

    Raises:
        RuntimeError: br could not be run, or its reply was not usable JSON. A stop rather
            than an empty list, which would read as a gate that has not run yet.
    """
    proc = br.run_br(repo_root, [GATE_KEY, "list", issue_id, "--robot"])
    try:
        payload = json.loads(proc.stdout)
    except ValueError as exc:
        raise RuntimeError(f"br gate list {issue_id} returned no usable JSON: {exc}") from exc
    results = payload.get("results") if isinstance(payload, dict) else None
    return [row for row in (results if isinstance(results, list) else []) if isinstance(row, dict)]


def read_gates(repo_root: Path, issue_id: str) -> list[dict]:
    """*issue_id*'s recorded gate rows, from whichever store the declared rung names.

    The gate query's one seam, so no caller branches on the mode itself. On
    :data:`owned_store.MODE_OWNED` the fold is the only answer and nothing is spawned; on
    :data:`owned_store.MODE_DUAL` both stores are read and br's answer returned, br being
    authoritative for reads on that rung.

    Raises:
        TrackerDivergenceError: the two stores disagree (:func:`refuse_disagreement`).
    """
    mode = owned_store.tracker_mode(repo_root)
    if mode == owned_store.MODE_OWNED:
        return owned_gates(repo_root, issue_id)
    rows = external_gates(repo_root, issue_id)
    if mode == owned_store.MODE_DUAL:
        refuse_disagreement(repo_root, issue_id, rows)
    return rows


def refuse_disagreement(
    repo_root: Path, issue_id: str, external: Iterable[Mapping[str, Any]]
) -> None:
    """Fail when the ledger's gate rows for *issue_id* contradict *external*.

    Raised rather than logged, which is `br._mirror_write`'s rule on the write path and
    §4.4 of `docs/requirements/work-tracker.md`'s on any disagreement: a finding, never a
    repair in place.

    **A record with no gate event in the ledger is history, not a disagreement.** No export
    carries a gate field (`differential.EXPORT_CANNOT_EXPRESS`), so every gate br recorded
    before the dual write began is missing from the ledger by construction — `baseline.py`'s
    excusal, applied per read. The bound that leaves: a ``br gate report`` run by hand on a
    record holding no other gate event is excused with it, which is the bypass
    `basicly tracker adopt` repairs (basicly-vkh0.24) and not one this read can tell apart.

    Raises:
        TrackerDivergenceError: both stores hold gate rows for *issue_id* and they differ.
    """
    owned = owned_gates(repo_root, issue_id)
    if not owned or _verdicts(owned) == _verdicts(external):
        return
    raise owned_store.TrackerDivergenceError(
        f"the two stores disagree about {issue_id}'s gates: br reports "
        f"{sorted(_verdicts(external))} and the owned ledger {sorted(_verdicts(owned))}; "
        "adopt a write that bypassed the seam with basicly tracker adopt"
    )


def _verdicts(rows: Iterable[Mapping[str, Any]]) -> set[tuple[str, str, bool]]:
    """*rows* as the set of verdicts they carry — what the two stores must share.

    A set because neither store promises a row order and both keep one row per
    ``(gate, provider)``. br's ``note`` is left out: the ledger never carried it, and an
    unmirrored field is not a disagreement about a verdict.
    """
    return {
        (str(row.get(GATE_KEY, "")), str(row.get(PROVIDER_KEY, "")), bool(row.get(PASSED_KEY)))
        for row in rows
    }
