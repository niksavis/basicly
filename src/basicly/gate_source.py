"""What gate results a record carries (basicly-vkh0.27).

Folded out of the owned ledger. What the rows *mean* stays in :mod:`basicly.policy`, this
module's one engine caller.

The boundary is *the gate query* against :mod:`basicly.tracker`, which owns the record and
ranking seams, and against :mod:`basicly.dependency_graph`, which owns the blocking graph.

The rows keep the shape the engine has always parsed, the way :func:`tracker.owned_record`
renders a record, so the caller has one parser.
"""

# comment-density-waiver: cohesion: 4 documented members plus the module contract over 300 tokens of
# code, so the share is set by the member count and not by narration — the same shape as
# `label_source` beside it. What is left is the two states an empty answer can mean, which
# is the one fact a caller cannot get from the code.

from __future__ import annotations

from pathlib import Path

from basicly import owned_store

# br's row keys, which are also the kit's payload keys (`gates.GATE_NAME_KEY` and its
# siblings): this module renders one shape into the other, so both vocabularies meet here.
GATE_KEY = "gate"
PROVIDER_KEY = "provider"
PASSED_KEY = "passed"


def owned_gates(repo_root: Path, issue_id: str) -> list[dict]:
    """*issue_id*'s gate rows as the owned ledger holds them, in br's row shape.

    Empty when no gate event named the record — `gates.GateFold.view`'s ordinary state, a
    gate nobody reported not being green. **A tombstoned record answers empty too**, the
    rule :func:`tracker.owned_record` gives: the two stores spell a deletion differently, and
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


def read_gates(repo_root: Path, issue_id: str) -> list[dict]:
    """*issue_id*'s recorded gate rows, folded out of the owned ledger.

    The gate query's one seam, so no caller reaches the store itself.
    """
    return owned_gates(repo_root, issue_id)
