"""Resumable loop-state reconstruction — pure reads from ``br`` (onb.6.1).

The harness keeps no durable side-state (architecture §24): everything the
loop needs to resume after a restart — or after switching agents mid-flight —
is reconstructed here by *reading* ``br``. Nothing in this module mutates the
tracker; it folds an issue's status, its stashed worktree/branch binding
(``external_ref``), its recorded gate verdicts, and its checkpoint/rework
comment markers into a single :class:`NodeState`, and derives a best-effort
loop *phase* from that recorded evidence.

Phase derivation is a reconstruction from what ``br`` records, not a transition
engine — the state machine (onb.6.3) owns advancement. Gate/checkpoint/rework
reads are delegated to the policy engine (onb.3) so the block-vs-advise rules
live in exactly one place. The ready and blocked sets come from the tracker
rather than being recomputed here, each through its own seam — ``tracker.read_ranking``
(basicly-vkh0.20) and :mod:`basicly.dependency_graph` (basicly-wpc8) — so §12.3's
rule survives the cutover in the form that mattered: both are the tracker's job
and this module only reads the answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import dependency_graph, policy, tracker, validate_gate
from .config import CHECKPOINTS, PolicyConfig, load_policy_config

# The loop phases, ordered from earliest to latest (architecture §23.3). "done"
# is terminal (the issue is closed); "intake" is the pre-classify default.
PHASES = ("intake", "classify", "decompose", "build", "verify", "validate", "ship", "done")

# external_ref encoding for an in-flight worktree binding. The state machine
# (onb.6.3) writes it with format_worktree_ref; this module is its only reader,
# so the schema lives here.
WORKTREE_REF_PREFIX = "worktree:"

# The ``br`` status vocabulary this repo knows, per ``br schema`` ($defs/Status).
# Named so the dispatch rule below can be written as the set it *admits*: the
# rule used to read ``status != "closed"``, which silently admitted every status
# beads added after it was written (basicly-toj6).
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

# The statuses under which a child is a candidate for sizing, funding and
# dispatch — and, the same thing read the other way, still holds its parent open.
# What is left out, and why:
#   closed, tombstone - terminal; the work is over.
#   deferred          - parked by a human. Admitting it made deferring a no-op:
#                       the bead stayed in the band table, counted toward the
#                       open-child total, and drew a per-lane assumption into the
#                       `cap x per-lane` forecast, so a deferred bead could
#                       refuse a pass its ready siblings could afford.
DISPATCHABLE_STATUSES = frozenset({"open", "in_progress", "blocked", "draft", "pinned"})


def is_dispatchable(status: str) -> bool:
    """True when a child in *status* may be sized, funded and dispatched.

    ``br`` lets a project define its own statuses (``workflow.status_groups.ready``
    in a store's own config, e.g. ``rework``), so a status outside
    :data:`KNOWN_STATUSES` is admitted rather than dropped: refusing an unknown
    status would both defund real work and let its parent fan in over it. The
    refusals are therefore exactly the named non-dispatchable statuses, each one a
    deliberate decision recorded above :data:`DISPATCHABLE_STATUSES`.
    """
    return status in DISPATCHABLE_STATUSES or status not in KNOWN_STATUSES


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
    has_children: bool
    # The issue's own title, carried so a state that has to say *why* a change was made
    # reads it off the state it already folded rather than paying a second tracker read
    # (``handoff.summary_payload``). Defaulted, so every other construction is unchanged.
    title: str = ""


# The dependency type a decomposition writes, and the direction is the load-bearing part:
# the *child* declares it onto its parent, so a record has children when an edge of this type
# points **at** it. Measured on this repo's log: 688 of 1072 asserted edges.
PARENT_CHILD = "parent-child"


def _has_children(record: dict) -> bool:
    """True when the issue has a parent-child dependent (it has been decomposed)."""
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
    """Reconstruct the furthest loop phase evidenced by an issue's tracker state.

    The ``ladder`` below is the answer, read strongest-first: the first rung whose
    evidence holds wins, and nothing under it is consulted.

    The ship rung requires the node to have landed, not just the checkpoint to
    be approved: a bound worktree whose verify gate is not green has not merged,
    so it derives as ``build`` and the next advance re-runs the build->verify
    landing. Without this, approving ship before the landing (e.g. after a
    landing failed on a transient ``.git/index.lock``) wedged the phase at ship
    with no route back to the merge (basicly-k35r). "Landed" holds when the
    worktree is gone (torn down after merge, or a feature with no binding) or
    when verify is green on the still-bound worktree (merged, pending teardown).

    A missing binding alone is *not* landed evidence, which is the hole k35r left
    open (basicly-jr0l.49): a leaf that never built has no binding either, and
    ``approve_checkpoint`` enforces no phase ordering, so a ship approval
    recorded out of order on an unstarted leaf derived ``ship`` and closed the
    bead with zero work done. The green required gate is what separates the two —
    the build->verify landing records it, and nothing a never-built node has run
    does — so every landed state must carry it.

    Both rungs read the **verify gate itself**, not ``gates.can_advance``: requiring
    a second gate otherwise dropped a merged node to ``build`` (basicly-u2hl.54.1).
    """
    if status == "closed":
        return "done"
    merged = "verify" in gates.required_passed
    validating = validate_gate.outstanding(gates)
    verified = merged and (worktree is not None or has_children)
    landed = merged and (worktree is None or verified)
    ladder = (
        # Approving ship early decides the *next* gate, never waives an unrun one;
        # `landed` rather than `verified` so a torn-down leaf still owes validation.
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
    """Reconstruct the loop state of *issue_id* purely from ``br`` (no mutation)."""
    config = config or load_policy_config(repo_root)
    # What this unit owes, so gate read, derived phase and rework tally share one set.
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
    """Every live record's loop phase, keyed by record, from **one** fold of the log.

    :func:`read_node_state` is per record by construction — seven whole-log reads each — so
    a phase for the whole population cost 138 s and was capped at the eight-record ready
    front instead (basicly-s1vqq2). `tracker.all_views` folds once and its view carries
    every value :func:`derive_phase` takes, so no cap is needed.

    **It calls the real derivation, and its inputs are the real readers too.** A second
    spelling of any of them is how two phases come to disagree while rendering identically,
    so the gate rows go through `policy.classify_gates` and the required set through
    `validate_gate.required_in`. The kit ships a `derive_phase` of its own and this is
    deliberately not it: that one folds the ledger alone and cannot see the level a unit's
    validate gate hangs off.
    """
    config = config or load_policy_config(repo_root)
    live = tracker.all_views(repo_root)
    parents = {
        edge.target
        for view in live.values()
        for edge in view.dependencies
        if edge.type == PARENT_CHILD
    }
    return {
        record: derive_phase(
            view.status,
            tuple(
                name for name in CHECKPOINTS if policy.checkpoint_approved_in(view.comments, name)
            ),
            parse_worktree_ref(view.external_ref),
            policy.classify_gates(
                [policy.GateVerdict(row.gate, row.provider, row.passed) for row in view.gates],
                validate_gate.required_in(view.comments, config),
            ),
            record in parents,
        )
        for record, view in live.items()
    }


# --- Ready / blocked sets ---------------------------------------------------


# The session walk lives in `policy` and is called there directly. This module
# used to carry a second copy that followed parent-child dependents only, which
# disagreed with the real one by 14 beads on `basicly-kjc5`; basicly-tcmy.30
# collapsed the two and basicly-tcmy.28 removed the re-export it left behind, so
# there is one name, in the module that owns what a session *is*.


@dataclass(frozen=True)
class RankedNode:
    """A ready issue with its scheduler rank and explainable score."""

    rank: int
    score: int
    issue_id: str
    title: str
    # The scorer's pre-evidence ordering. Kept because br's two diverge exactly
    # when evidence weighting mattered, and a recorded score is only interpretable
    # against the order it changed (basicly-vkh0.3). The owned scorer has no such
    # split — its score *is* the ordering — so it reports the two equal, which is
    # a fact about that policy and readable as one against `Ranking.schema`.
    fallback_rank: int = 0


@dataclass(frozen=True)
class Ranking:
    """One scheduler answer: its nodes plus the policy that produced them.

    The envelope matters as much as the ranks. D9 requires dispatch inputs to be
    reproducible, and a bare rank is not — it means nothing without the scoring
    policy behind it. Both scorers are explicitly versioned and state their own
    sort, so carrying those two strings is what makes a recorded rank
    interpretable later (basicly-vkh0.3) — and, since the cutover, what says
    *which* scorer produced it: ``tracker.scheduler.v1`` ranks on
    ``priority ASC, created_at ASC, id ASC``, while ``basicly.scheduler.v1`` drops
    the age term for ``priority ASC, dependents DESC, id ASC`` (basicly-vkh0.20).
    """

    nodes: tuple[RankedNode, ...]
    schema: str
    fallback_sort: str

    def by_issue(self) -> dict[str, RankedNode]:
        """The nodes keyed by issue id, for a dispatch-time lookup."""
        return {node.issue_id: node for node in self.nodes}


def ready_ranking(repo_root: Path, limit: int | None = None) -> Ranking:
    """The ready set ranked by whichever scorer the repo is flipped to.

    One parser over one payload shape. Which store answers is `basicly.tracker`'s
    business and not this module's — ``tracker.read_ranking`` is the seam, the way
    ``tracker.read_record`` is for a record, so the cutover stays in the module that
    owns it rather than putting a mode branch here (basicly-vkh0.19, .20).
    """
    payload = tracker.read_ranking(repo_root, limit)
    fallback = payload.get("fallback_policy")
    return Ranking(
        nodes=tuple(
            RankedNode(
                rank=int(rec["rank"]),
                score=int(rec.get("score", 0)),
                issue_id=str(rec["issue"]["id"]),
                title=str(rec["issue"].get("title", "")),
                # Absent on an older br: fall back to the rank itself, which is
                # what br does when evidence is tied or incomplete.
                fallback_rank=int(rec.get("fallback_rank", rec["rank"])),
            )
            for rec in payload.get("recommendations", [])
        ),
        schema=str(payload.get("schema", "")),
        fallback_sort=str(fallback.get("sort", "")) if isinstance(fallback, dict) else "",
    )


def ready_ranked(repo_root: Path, limit: int | None = None) -> tuple[RankedNode, ...]:
    """Return the ready issues ranked by the scheduler (highest priority first)."""
    return ready_ranking(repo_root, limit).nodes


def blocked_ids(repo_root: Path) -> tuple[str, ...]:
    """The ids waiting on a dependency; :mod:`basicly.dependency_graph` picks the store."""
    return dependency_graph.blocked(repo_root)
