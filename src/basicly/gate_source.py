from __future__ import annotations

from pathlib import Path

from basicly import owned_store

GATE_KEY = "gate"
PROVIDER_KEY = "provider"
PASSED_KEY = "passed"


def owned_gates(repo_root: Path, issue_id: str) -> list[dict]:

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

    return owned_gates(repo_root, issue_id)
