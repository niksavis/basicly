"""Checkpoint-gated loop state machine (onb.6.3).

The conductor of the harness: it advances one issue's track through the loop
phases (intake → classify → decompose → build → verify → ship → teardown),
enforcing the three human checkpoints and the bounded rework loop via the
policy engine (onb.3) and composing the already-built modules — classify
(onb.6.2), the decomposer (onb.4), worktree lifecycle (onb.1), the verify
runner (onb.2), and the serial merge queue (onb.5).

Thin conductor: the *agent* supplies the inputs a phase needs (the work type to
classify, the child plan to decompose) and does the actual coding in the
worktree; the engine records, gates, and advances. :func:`advance` is a single
resumable step — it re-reads the current phase from ``br`` (loop_state, onb.6.1)
every call and keeps no side-state, so a restart or an agent switch resumes
exactly where the tracker left off.

Because the phase is *derived* from ``br`` state, every step must either **block**
(waiting on an agent input, a human checkpoint, or a gate) or **produce a new
``br`` signal** that moves the derived phase forward — recording a type, creating
children, provisioning a worktree, recording a gate, or closing the issue. A
step never merely announces a move it did not make, so the resumable derivation
and the machine never disagree (and the :func:`run_until_blocked` driver cannot
spin).

Scope (recorded plan, Q3): this drives a single track. A decomposed feature fans
out one worktree per ready child and lands them through the serial merge queue
once they close; child tracks are advanced by re-invoking :func:`advance` per
child (the CLI/driver, onb.6.4, iterates them). Leaf types (bug/chore/task) skip
decomposition and build in their own worktree.

Lane mini-loop (factory design D7, basicly-kjc5.9): a *lane* is a build-phase
node that also has sub-task beads — write parallelism stops at depth 1, so a
lane never provisions worktrees of its own. Its sub-tasks are worked strictly in
sequence inside the lane's single worktree (one fresh runner dispatch each,
``fast`` verify each), and the lane signals merge-ready only once its
integration passes ``full`` verify plus the required validate gate. The lane's
own split into sub-tasks is engine-governed (the sizing governor plus
``[policy] max_subtasks_per_lane``), never a fourth human checkpoint.
"""

from __future__ import annotations

import contextlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from . import (
    classify,
    decisions,
    decompose,
    loop_state,
    merge,
    needs_input,
    policy,
    rubrics,
    run_record,
    runner,
    verify,
    worktree,
)
from .br import run_br as _run_br
from .config import PolicyConfig, load_policy_config, load_runner_config, load_worktree_config
from .decompose import ChildSpec

# Work classes that are leaf tracks — they build directly rather than decompose
# (architecture §12.1: bug/chore are leaves; a task is a unit of work).
_LEAF_TYPES = ("bug", "chore", "task")

# Phases whose transition merges a worktree back or tears one down and closes the
# issue. Git refuses to update a branch checked out in another worktree, so these
# must run from the base checkout; advancing them from a linked worktree once
# stranded a commit (child closed but unmerged) — the loop now refuses instead.
_BASE_CHECKOUT_PHASES = ("build", "ship")

# Verify modes chosen deterministically by change class (factory design D4): a
# sub-task inside a lane runs the fast suite, the lane's own integration runs the
# full one. Never an agent's judgment call, so neither reads Inputs.verify_mode.
_SUBTASK_VERIFY_MODE = "fast"
_LANE_VERIFY_MODE = "full"


@dataclass(frozen=True)
class Inputs:
    """Agent-supplied inputs a phase may need; absent ones cause a blocked result."""

    work_type: str | None = None
    children: tuple[ChildSpec, ...] | None = None
    verify_mode: str = "full"


@dataclass(frozen=True)
class AdvanceResult:
    """The outcome of one :func:`advance` step."""

    issue_id: str
    from_phase: str
    to_phase: str
    # "classified"|"decomposed"|"built"|"merged"|"shipped"|"tore-down"
    # |"done"|"sub-task"|"blocked"|"escalated"
    action: str
    detail: str = ""
    needs_input: str | None = None
    # The landing attempt behind this step, when one ran. Carried as data so a
    # driver can tell a scope collision from a red gate or an uncommitted
    # worktree without parsing the message (basicly-kjc5.20).
    landing: merge.MergeResult | None = None

    @property
    def advanced(self) -> bool:
        """True when the track moved to a new phase."""
        return self.to_phase != self.from_phase

    @property
    def progressed(self) -> bool:
        """True when the step did useful work, even without changing phase.

        A lane mini-loop step closes one sub-task and stays in ``build`` (the
        phase is derived, and the lane is still building) — real progress that
        neither :attr:`advanced` nor :attr:`blocked` captures, so drivers can
        keep iterating instead of mistaking it for a stall.
        """
        return self.advanced or self.action == "sub-task"

    @property
    def blocked(self) -> bool:
        """True when the track is waiting on an input, a checkpoint, or a gate."""
        return self.action in ("blocked", "escalated")


@dataclass(frozen=True)
class _Ctx:
    repo_root: Path
    issue_id: str
    state: loop_state.NodeState
    config: PolicyConfig
    inputs: Inputs


def _blocked(
    ctx: _Ctx,
    reason: str,
    *,
    action: str = "blocked",
    needs_input: str | None = None,
    landing: merge.MergeResult | None = None,
) -> AdvanceResult:
    return AdvanceResult(
        ctx.issue_id, ctx.state.phase, ctx.state.phase, action, reason, needs_input, landing
    )


def _moved(ctx: _Ctx, to_phase: str, action: str, detail: str = "") -> AdvanceResult:
    return AdvanceResult(ctx.issue_id, ctx.state.phase, to_phase, action, detail)


# --- Phase handlers ---------------------------------------------------------


def _on_intake(ctx: _Ctx) -> AdvanceResult:
    """Record the agent's proposed work type, then wait for the classify checkpoint.

    Recording the type does not itself leave intake — the derived phase advances
    to ``classify`` only when the human classify checkpoint is approved.
    """
    if not ctx.inputs.work_type:
        return _blocked(ctx, "classify needs an agent-proposed work type", needs_input="work_type")
    result = classify.classify(ctx.repo_root, ctx.issue_id, ctx.inputs.work_type)
    return _blocked(
        ctx, f"recorded work type {result.work_type!r}; classify checkpoint awaiting approval"
    )


def _on_classify(ctx: _Ctx) -> AdvanceResult:
    """Classify checkpoint is approved (that is why we are here): gate DoR, then branch.

    A leaf type provisions its own worktree; a feature/epic decomposes an
    agent-proposed child plan. Either action changes ``br`` so the derived phase
    moves forward.
    """
    dor = policy.definition_of_ready(ctx.repo_root, ctx.issue_id)
    if not dor.ready:
        return _blocked(ctx, f"definition of ready incomplete: {', '.join(dor.missing)}")
    if ctx.state.issue_type in _LEAF_TYPES:
        return _start_build_leaf(ctx)
    if not ctx.inputs.children:
        return _blocked(ctx, "decompose needs an agent-proposed child plan", needs_input="children")
    result = decompose.decompose(ctx.repo_root, ctx.issue_id, ctx.inputs.children)
    return _moved(
        ctx,
        "decompose",
        "decomposed",
        f"created {len(result.children)} children in {result.parallel_groups} group(s)",
    )


def _on_decompose(ctx: _Ctx) -> AdvanceResult:
    """Children exist: gate the decompose checkpoint, then fan out and land them."""
    if not policy.checkpoint_approved(ctx.repo_root, ctx.issue_id, "decompose"):
        return _blocked(ctx, "decompose checkpoint awaiting human approval")
    return _build_children(ctx)


def _on_build(ctx: _Ctx) -> AdvanceResult:
    """A worktree is bound: run the lane's next mini-loop step, or verify and land.

    A node whose package was split into sub-task beads is a lane (D7): its
    sub-tasks run in sequence inside this one worktree before it may land. A plain
    leaf has no sub-tasks and lands whatever its own dispatch committed.
    """
    if ctx.state.worktree is None:
        return _blocked(ctx, "build phase without a bound worktree")
    if ctx.inputs.children and not ctx.state.has_children:
        return _decompose_lane(ctx, ctx.inputs.children)
    if ctx.state.has_children:
        return _run_lane(ctx, ctx.state.worktree)
    return _verify_and_land(ctx, ctx.state.worktree.name)


def _on_verify(ctx: _Ctx) -> AdvanceResult:
    """Required gate is green (that is why we are here): gate the ship checkpoint."""
    if not policy.checkpoint_approved(ctx.repo_root, ctx.issue_id, "ship"):
        return _blocked(ctx, "ship checkpoint awaiting human approval")
    return _moved(ctx, "ship", "shipped", "ship checkpoint satisfied")


def _worktree_landed(repo_root: Path, binding: loop_state.WorktreeBinding) -> bool:
    """True when the worktree branch has landed on its base (or is already gone).

    A branch that no longer exists was merged and cleaned — ``git branch -d``
    refuses an unmerged branch, so a missing branch is proof it landed. An
    existing branch counts as landed only when its tip is an ancestor of the base
    HEAD, i.e. ``_verify_and_land`` really ran ``merge.merge_worktree``. This is
    the deterministic signal ``_on_ship`` uses to refuse closing a stranded node.
    """
    branch = binding.branch
    exists = (
        worktree.git(
            ["show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
            cwd=repo_root,
            check=False,
        ).returncode
        == 0
    )
    if not exists:
        return True
    session = worktree.load_session(binding.name, repo_root)
    base = session.base if session is not None else worktree.current_branch(repo_root)
    return (
        worktree.git(
            ["merge-base", "--is-ancestor", branch, base], cwd=repo_root, check=False
        ).returncode
        == 0
    )


def _on_ship(ctx: _Ctx) -> AdvanceResult:
    """Tear down the worktree, close the issue, and commit the tracker state.

    Guard: never close a leaf whose worktree branch has not landed on its base.
    The merge happens only in the build->verify transition (``_verify_and_land``);
    if that step was skipped — e.g. the verify gate was recorded out-of-band, so
    the derived phase jumped straight to verify — the code is stranded on the
    harness branch. Block with no side effects (no close, teardown, or tracker
    commit) instead of closing a bead whose work never merged.
    """
    binding = ctx.state.worktree
    if binding is not None:
        if not _worktree_landed(ctx.repo_root, binding):
            return _blocked(
                ctx,
                f"ship refuses to close: worktree branch {binding.branch!r} is not merged "
                "into its base — the build->verify landing was skipped (was the verify gate "
                "recorded out-of-band?); re-run the build->verify advance to land it first",
            )
        worktree.cleanup(binding.name, force=False, repo_root=ctx.repo_root)
    _run_br(ctx.repo_root, ["close", ctx.issue_id, "--reason", "shipped by the harness loop"])
    committed = merge.commit_tracker_state(
        ctx.repo_root, ctx.issue_id, action="close the shipped track"
    )
    detail = "worktree torn down and issue closed"
    if committed:
        detail += "; tracker state committed"
    return _moved(ctx, "done", "tore-down", detail)


# --- Build helpers ----------------------------------------------------------


def _start_build_leaf(ctx: _Ctx) -> AdvanceResult:
    """Provision the leaf's worktree and dispatch the selected runner in it.

    A headless runner does the node's coding before the block (§12.8); the
    manual handoff runner keeps the block-and-resume contract untouched. Either
    way this step blocks — the next advance verifies and lands whatever the
    agent committed.
    """
    wt_config = load_worktree_config(ctx.repo_root)
    active = len(worktree.list_sessions(ctx.repo_root))
    if active >= wt_config.concurrency:
        return _blocked(
            ctx,
            f"worktree concurrency cap reached ({active}/{wt_config.concurrency}); "
            "clean up a worktree or raise [worktree].concurrency in basicly.toml",
        )
    # Publish the claim: roll the pending tracker-only dirt (status, work type,
    # classify approval) into a chore commit now, so a teammate pulling the
    # repo sees the claim from the moment work starts, not at landing.
    merge.commit_tracker_state(
        ctx.repo_root, ctx.issue_id, action="record the claim before provisioning"
    )
    name = _worktree_name(ctx.issue_id)
    session = worktree.create(name, base=wt_config.base_branch, repo_root=ctx.repo_root)
    _bind_worktree(ctx, name, session.branch)
    return _dispatch_runner(ctx, name, Path(session.worktree_path))


def _dispatch_runner(ctx: _Ctx, name: str, cwd: Path) -> AdvanceResult:
    """Run the selected agent headless in the worktree; a handoff just blocks."""
    dispatch = _run_agent(ctx, ctx.issue_id, cwd)
    if dispatch.result.handoff:
        return _blocked(ctx, f"worktree {name!r} provisioned; awaiting the agent's work")
    held = _runner_block(ctx, dispatch, issue_id=ctx.issue_id, target=f"worktree {name!r}")
    return held or _blocked(
        ctx,
        f"runner {dispatch.spec.name!r} finished in worktree {name!r}; advance again to land it",
    )


@dataclass(frozen=True)
class _Dispatch:
    """One finished runner dispatch: what ran, where, and under which timeout."""

    spec: runner.RunnerSpec
    result: runner.RunResult
    cwd: Path
    timeout: float


def _run_agent(ctx: _Ctx, issue_id: str, cwd: Path) -> _Dispatch:
    """Dispatch *issue_id*'s prompt through the configured runner in *cwd*, recorded.

    The prompt is assembled per dispatch, so a lane's sequential sub-tasks each
    start from a fresh context that already sees the commits their predecessors
    made (D6/D7).
    """
    config = load_runner_config(ctx.repo_root)
    spec = runner.select_runner(config.specs, config.default, capable=runner.is_capable)
    result = runner.run(
        spec,
        dispatch_prompt(issue_id),
        cwd,
        capture_usage=True,
        timeout=config.runner_timeout,
    )
    record_run(ctx.repo_root, issue_id, spec, result)
    return _Dispatch(spec=spec, result=result, cwd=cwd, timeout=config.runner_timeout)


def _runner_block(
    ctx: _Ctx, dispatch: _Dispatch, *, issue_id: str, target: str
) -> AdvanceResult | None:
    """The blocked outcome of a finished dispatch, or None when it ran cleanly.

    One triage for both dispatch paths — a leaf's own worktree and a lane's
    sequential sub-tasks — so the hard-kill, failure, and needs-input contracts
    cannot drift apart. *issue_id* is the bead the outcome is attributed to
    (a sub-task, not its lane); *target* names where it ran, for the message.
    """
    spec, result = dispatch.spec, dispatch.result
    if result.timed_out:
        return _blocked(
            ctx,
            f"runner {spec.name!r} hit runner_timeout ({dispatch.timeout:.0f}s) "
            f"in {target}; inspect the worktree and re-dispatch",
        )
    if result.returncode != 0:
        tail = (result.stderr or result.stdout).strip().splitlines()
        detail = tail[-1] if tail else "no output"
        # A usage-capturing failure can leave one giant JSON envelope line on
        # stdout; cap the blocked reason so it stays a message, not a blob.
        if len(detail) > 200:
            detail = detail[:200] + "…"
        return _blocked(
            ctx, f"runner {spec.name!r} failed in {target} (exit {result.returncode}): {detail}"
        )
    # The agent finished cleanly but may have signalled it could not resolve a
    # required fact (basicly-o774): a needs-input sentinel maps to the loop's
    # block-and-resume contract so the missing fact is surfaced instead of the
    # loop landing a confident wrong answer. Consumed here so a re-dispatch (once
    # the fact is supplied) starts clean.
    needs = needs_input.take(dispatch.cwd)
    if needs is not None:
        # Durable trace (basicly-kjc5.3): the sentinel is consumed here, so the
        # marker comment is what the L3 lights-out precondition counts (D3).
        policy.record_needs_input(ctx.repo_root, issue_id, needs.fact)
        # And one queue item (basicly-kjc5.4): answerable via `loop answer`,
        # notified to the human, decidable by the decider under a grant.
        decisions.enqueue(ctx.repo_root, issue_id, "needs-input", needs.fact, needs.detail)
        reason = f"runner {spec.name!r} needs input in {target}: {needs.detail or needs.fact}"
        return _blocked(ctx, reason, needs_input=needs.fact)
    return None


def record_run(
    repo_root: Path, issue_id: str, spec: runner.RunnerSpec, result: runner.RunResult
) -> None:
    """Persist a metadata-only run-record for this dispatch, keyed by the bead.

    The command is redacted here (the prompt argument elided) before it is
    handed to the record, so no prompt or secret is ever persisted. Token
    telemetry (basicly-kjc5.1) rides along: adapter-reported usage where the
    CLI emits it, a flagged chars/4 estimate otherwise. Best-effort:
    a write failure must not fail the loop landing (same stance as the
    ``tool-usage`` telemetry hook), so an OS error is tolerated, not fatal.
    Shared with the supervisor's concurrent dispatch (basicly-kjc5.6) so both
    dispatch paths feed the one telemetry stream.
    """
    command: tuple[str, ...] = ()
    if not result.handoff:
        command = tuple(runner.format_command(spec, run_record.REDACTED_PROMPT, capture_usage=True))
    usage = runner.extract_usage(spec, result)
    entry = run_record.build_record(
        agent=spec.name,
        handoff=result.handoff,
        returncode=result.returncode,
        duration_s=result.duration_s,
        command=command,
        model=spec.model,
        tokens=usage.tokens if usage else None,
        cost=usage.cost if usage else None,
        estimated=usage.estimated if usage else None,
    )
    with contextlib.suppress(OSError):
        run_record.record(repo_root, issue_id, entry)


def dispatch_prompt(issue_id: str) -> str:
    """The agent-neutral dispatch prompt: point at the tracker, not at an agent."""
    return (
        f"You are in a git worktree dedicated to the tracked issue {issue_id}. "
        f"Read AGENTS.md for the repo rules, run `br show {issue_id}` for the "
        "requirement and acceptance criteria, implement the work, and commit it "
        "on the current branch referencing that issue id. Do not merge, push, or "
        "close the issue — the harness loop lands and ships it. "
        "If you exhaust your ability to resolve a required fact, do NOT guess: "
        f"write {needs_input.SENTINEL_FILE.as_posix()} as "
        '{"fact": "<the missing fact>", "detail": "<what you tried>"} and stop '
        "without committing a guess — the loop will block and surface it."
    )


def _verify_and_land(
    ctx: _Ctx, worktree_name: str, *, verify_mode: str | None = None
) -> AdvanceResult:
    """Land the worktree (merge re-verifies internally), then record the required gate."""
    mode = verify_mode or ctx.inputs.verify_mode
    result = merge.merge_worktree(ctx.repo_root, worktree_name, bead=ctx.issue_id, verify_mode=mode)
    if result.status == "not-ready":
        # The build's work is not committed on the branch: block with guidance,
        # do not burn a rework attempt on an operator-fixable state (basicly-4psl).
        return _blocked(ctx, result.detail, landing=result)
    if not result.merged:
        return _rework(ctx, merge.MERGE_GATE, f"merge failed: {result.detail}", landing=result)
    return _record_verify(ctx, result.detail, verify_mode=mode)


# --- Lane mini-loop: sequential sub-tasks in one worktree (basicly-kjc5.9) ----


def _run_lane(ctx: _Ctx, binding: loop_state.WorktreeBinding) -> AdvanceResult:
    """Run one step of the lane's mini-loop inside the lane's own worktree (D7).

    One advance = one step (run the next sub-task, or integrate), so the phase
    stays ``build`` throughout and a crash resumes mid-package straight from
    ``br`` like every other phase. Sub-tasks in a lane overlap by construction —
    a package splittable into disjoint scopes should have been split into
    top-level lanes — so they run strictly in sequence in this one worktree; the
    lane never provisions worktrees or spawns write-agents of its own.
    """
    subtasks = _child_states(ctx)
    cap = ctx.config.max_subtasks_per_lane
    if len(subtasks) > cap:
        return _blocked(
            ctx,
            f"lane carries {len(subtasks)} sub-task beads, over the [policy] "
            f"max_subtasks_per_lane bound ({cap}); flatten the extra work into more "
            "top-level packages instead of deepening this lane, or raise the bound",
        )
    session = worktree.load_session(binding.name, ctx.repo_root)
    if session is None:
        return _blocked(
            ctx, f"worktree {binding.name!r} has no session record; re-provision the lane"
        )

    open_ids = [cid for cid, status in subtasks if status != "closed"]
    if not open_ids:
        return _integrate_lane(ctx, binding, Path(session.worktree_path))
    blocked_ids = set(loop_state.blocked_ids(ctx.repo_root))
    runnable = [
        cid
        for cid in open_ids
        # A sub-task waiting on a queued judgment must not burn a dispatch that
        # would only re-block on the same missing answer (same stance as the
        # supervisor's readiness gate).
        if cid not in blocked_ids and not decisions.has_pending(ctx.repo_root, cid)
    ]
    if not runnable:
        return _blocked(
            ctx,
            f"lane sub-task(s) {', '.join(open_ids)} are all waiting on a dependency or a "
            "queued decision; answer the decision or unblock the graph, then advance again",
        )
    ordered = [cid for cid, _ in subtasks]
    return _run_subtask(
        ctx,
        runnable[0],
        session,
        position=ordered.index(runnable[0]) + 1,
        total=len(subtasks),
    )


def _decompose_lane(ctx: _Ctx, children: tuple[ChildSpec, ...]) -> AdvanceResult:
    """Record the agent-proposed sub-task plan for a lane already in build (D7).

    The same decompose engine the session level uses — so the sizing governor,
    scope-overlap grouping, and DoR-satisfying child bodies all apply — bounded
    additionally by ``max_subtasks_per_lane``. Recording sub-tasks does not move
    the derived phase (the lane stays in ``build``), so this step blocks; the next
    advance runs the first sub-task.
    """
    cap = ctx.config.max_subtasks_per_lane
    if len(children) > cap:
        return _blocked(
            ctx,
            f"lane plan proposes {len(children)} sub-tasks, over the [policy] "
            f"max_subtasks_per_lane bound ({cap}); propose more top-level packages "
            "instead of a deeper lane, or raise the bound",
        )
    result = decompose.decompose(ctx.repo_root, ctx.issue_id, children)
    return _blocked(
        ctx,
        f"recorded {len(result.children)} lane sub-task(s); advance again to run them in sequence",
    )


def _run_subtask(
    ctx: _Ctx, subtask_id: str, session: worktree.Session, *, position: int, total: int
) -> AdvanceResult:
    """Dispatch one sub-task fresh in the lane worktree, then ``fast``-verify it.

    A fresh dispatch per sub-task is the point (D7/D8): the prompt is rebuilt from
    ``br`` and the runner starts on a clean context that already sees the commits
    its predecessors made. The commit-presence check makes the step idempotent —
    a handoff runner blocks for the driving agent, and the next advance verifies
    the commit rather than re-dispatching the same sub-task. A passing ``fast``
    verify closes the sub-task, which is what advances the lane; a failure is
    bounded on the sub-task's own rework record, so one bad sub-task escalates
    instead of consuming the whole lane's budget.
    """
    cwd = Path(session.worktree_path)
    where = f"sub-task {position}/{total} ({subtask_id})"
    if not _subtask_committed(subtask_id, session):
        dispatch = _run_agent(ctx, subtask_id, cwd)
        if dispatch.result.handoff:
            return _blocked(
                ctx,
                f"{where} dispatched in worktree {session.name!r}; awaiting the agent's work",
            )
        held = _runner_block(ctx, dispatch, issue_id=subtask_id, target=where)
        if held is not None:
            return held
        if not _subtask_committed(subtask_id, session):
            return _rework(
                ctx,
                verify.DEFAULT_GATE,
                f"{where}: runner {dispatch.spec.name!r} finished without committing anything "
                f"referencing {subtask_id} on {session.branch}",
                issue_id=subtask_id,
            )
    report = verify.run_verify(cwd, _SUBTASK_VERIFY_MODE)
    record = run_record.latest_record(ctx.repo_root, subtask_id)
    verify.report_gate(ctx.repo_root, subtask_id, report, actor=record.agent if record else None)
    if not report.passed:
        return _rework(
            ctx,
            verify.DEFAULT_GATE,
            f"{where}: verify {_SUBTASK_VERIFY_MODE} failed: {', '.join(report.failures)}",
            issue_id=subtask_id,
        )
    _run_br(
        ctx.repo_root,
        ["close", subtask_id, "--reason", f"lane sub-task verified in {ctx.issue_id}"],
    )
    return AdvanceResult(
        ctx.issue_id,
        ctx.state.phase,
        ctx.state.phase,
        "sub-task",
        f"{where} verified and closed; advance again for the next lane step",
    )


def references_bead(message: str, bead_id: str) -> bool:
    """True when *message* references *bead_id* as a whole id, not as a prefix.

    Bead ids nest by suffix (``x.1`` and ``x.10``), so a plain substring test
    would read ``x.10``'s commit as proof that ``x.1`` was done — enough to close
    a sub-task nobody worked on. The id must therefore not be followed by another
    id character.
    """
    return re.search(rf"{re.escape(bead_id)}(?![0-9A-Za-z._-])", message) is not None


def _subtask_committed(subtask_id: str, session: worktree.Session) -> bool:
    """True when the lane branch carries a commit referencing *subtask_id*.

    The deterministic "did this sub-task's work actually happen" signal, and the
    reason the step is safe to re-enter: every dispatch prompt (and this repo's
    commit-msg gate) requires a commit to reference its bead id, so a commit
    naming the sub-task since the lane forked is proof of work — the same stance
    as the merge queue's not-ready guard, with no extra state to keep. ``git
    grep``'s fixed-string match is only a prefilter; :func:`references_bead`
    decides, so a sibling id that merely starts with this one cannot pass.
    """
    proc = worktree.git(
        [
            "log",
            f"{session.base_head}..HEAD",
            "--fixed-strings",
            f"--grep={subtask_id}",
            "--format=%B%x00",
        ],
        cwd=Path(session.worktree_path),
        check=False,
    )
    if proc.returncode != 0:
        return False
    return any(references_bead(message, subtask_id) for message in proc.stdout.split("\0"))


def _integrate_lane(ctx: _Ctx, binding: loop_state.WorktreeBinding, cwd: Path) -> AdvanceResult:
    """Every sub-task closed: validate the lane, then land it under ``full`` verify.

    Order matters. Validate (D4: the behavioral ``rubric`` gate, advisory at
    sub-task level and **required** here) runs on the lane's own tree *before* the
    landing, because the landing merges the moment its verify passes — a validate
    failure has to stop the lane while its work is still unmerged. Integration
    itself is that landing: it rebases the lane onto the current base, re-runs the
    deterministic suite in ``full`` mode, and records the required verify gate.
    Nothing records a passing verify gate ahead of the merge, which would derive
    the phase past ``build`` and strand the branch.
    """
    validate = _validate_lane(ctx, cwd)
    if validate is not None:
        return validate
    return _verify_and_land(ctx, binding.name, verify_mode=_LANE_VERIFY_MODE)


def _validate_lane(ctx: _Ctx, cwd: Path) -> AdvanceResult | None:
    """Evaluate the lane's behavioral rubrics; None when validate passes.

    D4: validate is acceptance-criteria satisfaction — the ``rubric`` gate
    promoted from advisory to **required** at lane level. The promotion belongs to
    the level, not to ``[policy] required_gates``, so a consumer's gate list
    cannot silently drop it. Judged checks stay advisory inside
    :func:`rubrics.gate_status` (a subjective verdict never blocks a merge on its
    own), and a work class no rubric covers has nothing to validate.
    """
    selected = rubrics.select_rubrics(rubrics.load_rubrics(), ctx.state.issue_type)
    if not selected:
        return None
    verdicts = [
        verdict for rubric in selected for verdict in rubrics.evaluate(ctx.issue_id, rubric, cwd)
    ]
    rubrics.report_gate(ctx.repo_root, ctx.issue_id, verdicts)
    if rubrics.gate_status(verdicts) != "fail":
        return None
    failed = ", ".join(
        verdict.check_id
        for verdict in verdicts
        if verdict.kind == rubrics.DETERMINISTIC and verdict.answer == rubrics.NO
    )
    return _rework(ctx, rubrics.RUBRIC_GATE, f"lane validate failed: {failed}")


def _build_children(ctx: _Ctx) -> AdvanceResult:
    """Fan out a worktree per ready child; once all close, land those still live.

    A child driven through its own loop lands and tears down its worktree before
    closing, so only children with a live session go through the merge queue —
    the rest already self-landed.
    """
    children = _child_states(ctx)
    if not children:
        return _blocked(ctx, "decompose approved but no child tracks are recorded")
    _ensure_child_worktrees(ctx, children)
    still_open = [cid for cid, status in children if status != "closed"]
    if still_open:
        return _blocked(ctx, f"building: {len(still_open)} child track(s) still open")

    items = [(_worktree_name(cid), cid) for cid, _ in children]
    live = {session.name for session in worktree.list_sessions(ctx.repo_root)}
    pending = [(name, cid) for name, cid in items if name in live]
    if pending:
        results = merge.merge_queue(
            ctx.repo_root, pending, config=ctx.config, verify_mode=ctx.inputs.verify_mode
        )
        failed = next((q for q in results if not q.result.merged), None)
        if failed is not None:
            action = "escalated" if failed.escalate else "blocked"
            reason = f"merge failed for {failed.result.name}: {failed.result.detail}"
            return _blocked(ctx, reason, action=action)
    detail = f"merged {len(pending)} child worktree(s)"
    if len(pending) < len(items):
        detail += f"; {len(items) - len(pending)} already self-landed"
    return _record_verify(ctx, detail)


def _record_verify(ctx: _Ctx, detail: str, *, verify_mode: str | None = None) -> AdvanceResult:
    """Run verify + record the required gate so the derived phase becomes verify."""
    report = verify.run_verify(ctx.repo_root, verify_mode or ctx.inputs.verify_mode)
    record = run_record.latest_record(ctx.repo_root, ctx.issue_id)
    verify.report_gate(ctx.repo_root, ctx.issue_id, report, actor=record.agent if record else None)
    if not report.passed:
        return _rework(ctx, verify.DEFAULT_GATE, f"verify failed: {', '.join(report.failures)}")
    return _moved(ctx, "verify", "merged", detail)


def _rework(
    ctx: _Ctx,
    gate: str,
    reason: str,
    *,
    issue_id: str | None = None,
    landing: merge.MergeResult | None = None,
) -> AdvanceResult:
    """Record a rework attempt for *gate* and block, escalating at the cap.

    An escalation is a human judgment call, so it also enters the decision
    queue (basicly-kjc5.4) — one surface for everything blocked on a decision.
    *issue_id* attributes the attempt to a bead other than the node itself: a
    lane's sub-task is bounded on its own record, so one bad sub-task escalates
    rather than spending the whole lane's rework budget. *landing* carries the
    merge attempt that failed, so a driver can route a scope collision
    differently from a red gate (basicly-kjc5.20).
    """
    target = issue_id or ctx.issue_id
    attempts = policy.record_rework(ctx.repo_root, target, gate)
    action = "escalated" if attempts >= ctx.config.max_rework else "blocked"
    if action == "escalated":
        decisions.enqueue(
            ctx.repo_root,
            target,
            "escalation",
            f"rework cap reached on gate {gate}: retry, re-dispatch, or park?",
            reason,
        )
    return _blocked(
        ctx,
        f"{reason} (rework {attempts}/{ctx.config.max_rework})",
        action=action,
        landing=landing,
    )


def _ensure_child_worktrees(ctx: _Ctx, children: list[tuple[str, str]]) -> None:
    """Provision a worktree for each dependency-unblocked, still-open child, up to the cap."""
    wt_config = load_worktree_config(ctx.repo_root)
    existing = {session.name for session in worktree.list_sessions(ctx.repo_root)}
    room = wt_config.concurrency - len(existing)
    ready = {node.issue_id for node in loop_state.ready_ranked(ctx.repo_root)}
    # Publish the fan-out claims the same way a leaf publishes its own.
    merge.commit_tracker_state(
        ctx.repo_root, ctx.issue_id, action="record the claim before provisioning"
    )
    for cid, status in children:
        if room <= 0:
            break
        name = _worktree_name(cid)
        if status == "closed" or name in existing or cid not in ready:
            continue
        session = worktree.create(name, base=wt_config.base_branch, repo_root=ctx.repo_root)
        _bind_worktree(ctx, name, session.branch, issue_id=cid)
        existing.add(name)
        room -= 1


def _bind_worktree(ctx: _Ctx, name: str, branch: str, *, issue_id: str | None = None) -> None:
    """Stash the worktree/branch binding on the issue's external_ref."""
    ref = loop_state.format_worktree_ref(name, branch)
    _run_br(ctx.repo_root, ["update", issue_id or ctx.issue_id, "--external-ref", ref])


def _child_states(ctx: _Ctx) -> list[tuple[str, str]]:
    """Return ``(child_id, status)`` for each parent-child dependent of the node."""
    proc = _run_br(ctx.repo_root, ["show", ctx.issue_id, "--json"])
    data = json.loads(proc.stdout)
    record = data[0] if isinstance(data, list) else data
    dependents = record.get("dependents") or []
    return [
        (str(dep["id"]), str(dep.get("status", "")))
        for dep in dependents
        if isinstance(dep, dict) and dep.get("dependency_type") == "parent-child" and "id" in dep
    ]


def _worktree_name(issue_id: str) -> str:
    """A filesystem/branch-safe worktree name derived from an issue id."""
    return issue_id.replace(".", "-")


# --- Public entry points ----------------------------------------------------

_HANDLERS = {
    "intake": _on_intake,
    "classify": _on_classify,
    "decompose": _on_decompose,
    "build": _on_build,
    "verify": _on_verify,
    "ship": _on_ship,
}


def advance(
    repo_root: Path,
    issue_id: str,
    *,
    config: PolicyConfig | None = None,
    inputs: Inputs | None = None,
) -> AdvanceResult:
    """Advance *issue_id* one loop phase, resuming from its ``br`` state.

    Reads the current phase from the tracker, dispatches to the phase handler,
    and returns the transition outcome. Blocks (rather than raising) when an
    input is missing or a checkpoint/gate is not yet satisfied.
    """
    config = config or load_policy_config(repo_root)
    inputs = inputs or Inputs()
    state = loop_state.read_node_state(repo_root, issue_id, config)
    if state.phase == "done":
        return AdvanceResult(issue_id, "done", "done", "done", "already shipped")

    ctx = _Ctx(repo_root, issue_id, state, config, inputs)
    if state.phase in _BASE_CHECKOUT_PHASES and worktree.is_linked_checkout(repo_root):
        return _blocked(
            ctx,
            f"the {state.phase!r} transition merges/ships and must run from the base "
            f"checkout, not a linked worktree ({repo_root}); cd to the base checkout "
            "and re-run 'basicly loop advance'",
            needs_input="base-checkout",
        )
    return _HANDLERS[state.phase](ctx)


def run_until_blocked(
    repo_root: Path,
    issue_id: str,
    *,
    config: PolicyConfig | None = None,
    inputs: Inputs | None = None,
    max_steps: int = 20,
) -> list[AdvanceResult]:
    """Advance repeatedly until the track blocks, finishes, or hits *max_steps*.

    A thin driver over :func:`advance`; each step re-reads ``br`` so the loop
    stays resumable. Stops as soon as a step blocks or reaches ``done`` — a
    human/agent then resolves the block and re-invokes. A lane mini-loop step
    neither blocks nor changes phase, so a headless lane runs its sub-tasks in
    sequence within one call (bounded by *max_steps*).
    """
    results: list[AdvanceResult] = []
    for _ in range(max_steps):
        result = advance(repo_root, issue_id, config=config, inputs=inputs)
        results.append(result)
        if result.blocked or result.to_phase == "done":
            break
    return results
