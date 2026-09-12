from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import dependency_graph, policy, tracker, validate_gate
from .config import CHECKPOINTS, PolicyConfig, load_policy_config

PHASES = ("intake", "classify", "decompose", "build", "verify", "validate", "ship", "done")

WORKTREE_REF_PREFIX = "worktree:"

KNOWN_STATUSES = frozenset({
    "open",
    "in_progress",
    "blocked",
    "deferred",
    "draft",
    "closed",
    "tombstone",
    "pinned",
})

DISPATCHABLE_STATUSES = frozenset({"open", "in_progress", "blocked", "draft", "pinned"})


def is_dispatchable(status: str) -> bool:

    return status in DISPATCHABLE_STATUSES or status not in KNOWN_STATUSES


@dataclass(frozen=True)
class WorktreeBinding:
    name: str
    branch: str


def format_worktree_ref(name: str, branch: str) -> str:
    return f"{WORKTREE_REF_PREFIX}{name}:{branch}"


def parse_worktree_ref(external_ref: str | None) -> WorktreeBinding | None:
    if not external_ref or not external_ref.startswith(WORKTREE_REF_PREFIX):
        return None
    name, sep, branch = external_ref[len(WORKTREE_REF_PREFIX) :].partition(":")
    if not sep or not name or not branch:
        return None
    return WorktreeBinding(name=name, branch=branch)


@dataclass(frozen=True)
class NodeState:
    issue_id: str
    status: str
    issue_type: str
    phase: str
    worktree: WorktreeBinding | None
    gates: policy.GateStatus
    checkpoints: tuple[str, ...]
    rework: dict[str, int]
    has_children: bool
    title: str = ""


PARENT_CHILD = "parent-child"


def _has_children(record: dict) -> bool:
    dependents = record.get("dependents") or []
    return any(
        isinstance(dep, dict) and dep.get("dependency_type") == PARENT_CHILD for dep in dependents
    )


def derive_phase(
    status: str,
    checkpoints: tuple[str, ...],
    worktree: WorktreeBinding | None,
    gates: policy.GateStatus,
    has_children: bool,
) -> str:

    if status == "closed":
        return "done"
    merged = "verify" in gates.required_passed
    validating = validate_gate.outstanding(gates)
    verified = merged and (worktree is not None or has_children)
    landed = merged and (worktree is None or verified)
    ladder = (
        ("ship", "ship" in checkpoints and landed and not validating),
        ("validate", landed and validating),
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
    config = config or load_policy_config(repo_root)
    config = validate_gate.required_config(repo_root, issue_id, config)
    record = tracker.require_record(repo_root, issue_id)

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
        has_children=has_children,
        title=str(record.get("title", "")),
    )


def phase_map(repo_root: Path, config: PolicyConfig | None = None) -> dict[str, str]:

    return {record: held[0] for record, held in state_map(repo_root, config).items()}


def state_map(
    repo_root: Path, config: PolicyConfig | None = None
) -> dict[str, tuple[str, bool, str]]:

    config = config or load_policy_config(repo_root)
    live = tracker.all_views(repo_root)
    parents = {
        edge.target
        for view in live.values()
        for edge in view.dependencies
        if edge.type == PARENT_CHILD
    }
    built: dict[str, tuple[str, bool, str]] = {}
    for record, view in live.items():
        gates = policy.classify_gates(
            [policy.GateVerdict(row.gate, row.provider, row.passed) for row in view.gates],
            validate_gate.required_in(view.comments, config),
        )
        built[record] = (
            derive_phase(
                view.status,
                tuple(
                    name
                    for name in CHECKPOINTS
                    if policy.checkpoint_approved_in(view.comments, name)
                ),
                parse_worktree_ref(view.external_ref),
                gates,
                record in parents,
            ),
            gates.can_advance,
            view.status,
        )
    return built


@dataclass(frozen=True)
class RankedNode:
    rank: int
    score: int
    issue_id: str
    title: str
    fallback_rank: int = 0


@dataclass(frozen=True)
class Ranking:
    nodes: tuple[RankedNode, ...]
    schema: str
    fallback_sort: str

    def by_issue(self) -> dict[str, RankedNode]:
        return {node.issue_id: node for node in self.nodes}


def ready_ranking(repo_root: Path, limit: int | None = None) -> Ranking:

    payload = tracker.read_ranking(repo_root, limit)
    fallback = payload.get("fallback_policy")
    return Ranking(
        nodes=tuple(
            RankedNode(
                rank=int(rec["rank"]),
                score=int(rec.get("score", 0)),
                issue_id=str(rec["issue"]["id"]),
                title=str(rec["issue"].get("title", "")),
                fallback_rank=int(rec.get("fallback_rank", rec["rank"])),
            )
            for rec in payload.get("recommendations", [])
        ),
        schema=str(payload.get("schema", "")),
        fallback_sort=str(fallback.get("sort", "")) if isinstance(fallback, dict) else "",
    )


def ready_ranked(repo_root: Path, limit: int | None = None) -> tuple[RankedNode, ...]:
    return ready_ranking(repo_root, limit).nodes


def blocked_ids(repo_root: Path) -> tuple[str, ...]:
    return dependency_graph.blocked(repo_root)
