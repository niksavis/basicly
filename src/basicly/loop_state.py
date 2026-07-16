"""Resumable loop-state reconstruction — pure reads from ``br`` (onb.6.1).

The harness keeps no durable side-state (architecture §12.7): everything the
loop needs to resume after a restart — or after switching agents mid-flight —
is reconstructed here by *reading* ``br``. Nothing in this module mutates the
tracker; it folds an issue's status, its stashed worktree/branch binding
(``external_ref``), its recorded gate verdicts, and its checkpoint/rework
comment markers into a single :class:`NodeState`, and derives a best-effort
loop *phase* from that recorded evidence.

Phase derivation is a reconstruction from what ``br`` records, not a transition
engine — the state machine (onb.6.3) owns advancement. Gate/checkpoint/rework
reads are delegated to the policy engine (onb.3) so the block-vs-advise rules
live in exactly one place. The ready and blocked sets come straight from ``br``
(``br scheduler``/``br ready``/``br blocked``); scheduling is ``br``'s job, not
ours (§12.3).

Inherited ``agent_context`` is surfaced when present and simply reads as
``None`` when the tracker has ``inherited_context`` disabled — its absence is a
supported state, never an error.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from . import policy
from .br import run_br as _run_br
from .config import CHECKPOINTS, PolicyConfig, load_policy_config

# The loop phases, ordered from earliest to latest (architecture §12.2). "done"
# is terminal (the issue is closed); "intake" is the pre-classify default.
PHASES = ("intake", "classify", "decompose", "build", "verify", "ship", "done")

# external_ref encoding for an in-flight worktree binding. The state machine
# (onb.6.3) writes it with format_worktree_ref; this module is its only reader,
# so the schema lives here.
WORKTREE_REF_PREFIX = "worktree:"


# --- Worktree binding (external_ref) ----------------------------------------


@dataclass(frozen=True)
class WorktreeBinding:
    """The worktree/branch an issue is being built in, stashed on its external_ref."""

    name: str
    branch: str


def format_worktree_ref(name: str, branch: str) -> str:
    """Encode a worktree binding for ``br update --external-ref``."""
    return f"{WORKTREE_REF_PREFIX}{name}:{branch}"


def parse_worktree_ref(external_ref: str | None) -> WorktreeBinding | None:
    """Parse a worktree binding from an ``external_ref``; None when it is unset/foreign."""
    if not external_ref or not external_ref.startswith(WORKTREE_REF_PREFIX):
        return None
    name, sep, branch = external_ref[len(WORKTREE_REF_PREFIX) :].partition(":")
    if not sep or not name or not branch:
        return None
    return WorktreeBinding(name=name, branch=branch)


# --- Node state -------------------------------------------------------------


@dataclass(frozen=True)
class NodeState:
    """The reconstructed loop state of one issue, folded from ``br`` reads."""

    issue_id: str
    status: str
    issue_type: str
    phase: str
    worktree: WorktreeBinding | None
    gates: policy.GateStatus
    checkpoints: tuple[str, ...]
    rework: dict[str, int]
    agent_context: str | None
    has_children: bool


def _show(repo_root: Path, issue_id: str) -> dict:
    """Return the raw ``br show --json`` record for *issue_id*."""
    proc = _run_br(repo_root, ["show", issue_id, "--json"])
    data = json.loads(proc.stdout)
    record = data[0] if isinstance(data, list) else data
    if not isinstance(record, dict):
        raise RuntimeError(f"br show {issue_id} returned no issue record")
    return record


def _has_children(record: dict) -> bool:
    """True when the issue has a parent-child dependent (it has been decomposed)."""
    dependents = record.get("dependents") or []
    return any(
        isinstance(dep, dict) and dep.get("dependency_type") == "parent-child" for dep in dependents
    )


def derive_phase(
    status: str,
    checkpoints: tuple[str, ...],
    worktree: WorktreeBinding | None,
    gates: policy.GateStatus,
    has_children: bool,
) -> str:
    """Reconstruct the furthest loop phase evidenced by an issue's ``br`` state.

    Reads the strongest recorded signal downward: a closed issue is done; an
    approved ship checkpoint means shipping; green required gates on a bound
    worktree mean verify passed; a bound worktree means building; a decompose
    checkpoint (or existing children) means decomposed; a classify checkpoint
    means classified. Everything else is still intake.
    """
    if status == "closed":
        return "done"
    verified = gates.can_advance and (worktree is not None or has_children)
    ladder = (
        ("ship", "ship" in checkpoints),
        ("verify", verified),
        ("build", worktree is not None),
        ("decompose", "decompose" in checkpoints or has_children),
        ("classify", "classify" in checkpoints),
    )
    for phase, reached in ladder:
        if reached:
            return phase
    return "intake"


def read_node_state(
    repo_root: Path, issue_id: str, config: PolicyConfig | None = None
) -> NodeState:
    """Reconstruct the loop state of *issue_id* purely from ``br`` (no mutation)."""
    config = config or load_policy_config(repo_root)
    record = _show(repo_root, issue_id)

    worktree = parse_worktree_ref(record.get("external_ref"))
    gates = policy.gate_status(repo_root, issue_id, config)
    checkpoints = tuple(
        name for name in CHECKPOINTS if policy.checkpoint_approved(repo_root, issue_id, name)
    )
    rework = {
        gate: policy.rework_attempts(repo_root, issue_id, gate) for gate in config.required_gates
    }
    has_children = _has_children(record)
    status = str(record.get("status", ""))

    return NodeState(
        issue_id=issue_id,
        status=status,
        issue_type=str(record.get("issue_type", "")),
        phase=derive_phase(status, checkpoints, worktree, gates, has_children),
        worktree=worktree,
        gates=gates,
        checkpoints=checkpoints,
        rework=rework,
        agent_context=record.get("agent_context"),
        has_children=has_children,
    )


# --- Ready / blocked sets ---------------------------------------------------


@dataclass(frozen=True)
class RankedNode:
    """A ready issue with its ``br scheduler`` rank and explainable score."""

    rank: int
    score: int
    issue_id: str
    title: str


def ready_ranked(repo_root: Path, limit: int | None = None) -> tuple[RankedNode, ...]:
    """Return the ready issues ranked by ``br scheduler`` (highest priority first)."""
    args = ["scheduler", "--json"]
    if limit is not None:
        args += ["--limit", str(limit)]
    proc = _run_br(repo_root, args)
    recommendations = json.loads(proc.stdout).get("recommendations", [])
    return tuple(
        RankedNode(
            rank=int(rec["rank"]),
            score=int(rec.get("score", 0)),
            issue_id=str(rec["issue"]["id"]),
            title=str(rec["issue"].get("title", "")),
        )
        for rec in recommendations
    )


def blocked_ids(repo_root: Path) -> tuple[str, ...]:
    """Return the ids of issues that are blocked (waiting on a dependency)."""
    proc = _run_br(repo_root, ["blocked", "--json"])
    issues = json.loads(proc.stdout)
    return tuple(str(issue["id"]) for issue in issues if isinstance(issue, dict) and "id" in issue)
