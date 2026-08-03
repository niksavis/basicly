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
from collections.abc import Mapping
from dataclasses import dataclass, replace
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
from .config import (
    PolicyConfig,
    load_policy_config,
    load_runner_config,
    load_sizing_config,
    load_worktree_config,
)
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
    # |"done"|"sub-task"|"blocked"|"escalated"|"decision"
    action: str
    detail: str = ""
    needs_input: str | None = None
    # The landing attempt behind this step, when one ran. Carried as data so a
    # driver can tell a scope collision from a red gate or an uncommitted
    # worktree without parsing the message (basicly-kjc5.20).
    landing: merge.MergeResult | None = None
    # The human checkpoint this step is waiting on, when the block is a
    # checkpoint block. Same stance as ``landing``: a ceremony driver resolves
    # the checkpoint from data, never by sniffing the detail string
    # (basicly-kjc5.41).
    checkpoint: str | None = None

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
        """True when the track is waiting on an input, a checkpoint, or a gate.

        ``decision`` counts: a lane holding on a queued validate dispute (D4
        amended) is waiting on a human exactly like an escalation, and if it were
        neither blocked nor progressed the CLI would exit 0 on a lane that did not
        land — a silent stall.
        """
        return self.action in ("blocked", "escalated", "decision")


@dataclass(frozen=True)
class _Ctx:
    repo_root: Path
    issue_id: str
    state: loop_state.NodeState
    config: PolicyConfig
    inputs: Inputs
    # The issue carrying the grant ledger for this session, when the caller named
    # one (``loop run --root``). None leaves the dispatch cost gates inert, which is
    # what every caller that never had a session root already got (basicly-1th1).
    grant_root: str | None = None


def _blocked(  # noqa: PLR0913 — one keyword per block shape, all mutually exclusive
    ctx: _Ctx,
    reason: str,
    *,
    action: str = "blocked",
    needs_input: str | None = None,
    landing: merge.MergeResult | None = None,
    checkpoint: str | None = None,
) -> AdvanceResult:
    return AdvanceResult(
        ctx.issue_id,
        ctx.state.phase,
        ctx.state.phase,
        action,
        reason,
        needs_input,
        landing,
        checkpoint,
    )


def _moved(ctx: _Ctx, to_phase: str, action: str, detail: str = "") -> AdvanceResult:
    return AdvanceResult(ctx.issue_id, ctx.state.phase, to_phase, action, detail)


def _evidence_block(ctx: _Ctx, root: Path | None = None) -> AdvanceResult | None:
    """Refuse the step when this phase's declared evidence artifact is absent (m4zv.13).

    None when the phase declares nothing — the default, and then this costs one
    dict lookup and touches neither disk nor tracker. When a declaration *is*
    satisfied the path is recorded on the bead here, before the transition rather
    than after it: ``_on_ship`` commits the tracker state, so a marker written
    afterwards would sit in the local db only (the ordering
    :func:`_record_cost_rollup` needs, for the same reason).

    *root* is the checkout the phase's work happened in; ``None`` means the one the
    advance is running in.
    """
    status = policy.evidence_status(root or ctx.repo_root, ctx.config, ctx.state.phase)
    if not status.satisfied:
        return _blocked(ctx, status.reason, needs_input="evidence")
    if status.declared is not None:
        policy.record_evidence(ctx.repo_root, ctx.issue_id, ctx.state.phase, status.declared)
    return None


def _record_gate(ctx: _Ctx, issue_id: str, report: verify.VerifyReport) -> str | None:
    """Record *report* as the verify gate; return a reason when the tracker refused.

    :func:`verify.report_gate` degrades gracefully so a missing tracker never
    masks the verify result — but the caller must not then carry on as if the
    gate were recorded. :func:`loop_state.derive_phase` keys off
    ``gates.can_advance``, so an unrecorded gate derives the node back to
    ``build`` and the next advance re-runs build->verify: a loop that never
    progresses and never says why. Surfacing the tracker's own message turns
    that into one blocked result naming the cause (basicly-o7z5).
    """
    record = run_record.latest_record(ctx.repo_root, issue_id)
    ok, message = verify.report_gate(
        ctx.repo_root, issue_id, report, actor=record.agent if record else None
    )
    return None if ok else f"verify gate not recorded on {issue_id}: {message}"


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
        ctx,
        f"recorded work type {result.work_type!r}; classify checkpoint awaiting approval",
        checkpoint="classify",
    )


def _on_classify(ctx: _Ctx) -> AdvanceResult:
    """Classify checkpoint is approved (that is why we are here): gate DoR, then branch.

    A leaf type provisions its own worktree; a feature/epic decomposes an
    agent-proposed child plan. Either action changes ``br`` so the derived phase
    moves forward.
    """
    dor = policy.definition_of_ready(ctx.repo_root, ctx.issue_id)
    if not dor.ready:
        # Hand back the remedy, not just the complaint. This refusal is where an
        # agent used to *discover* the required sections — a read, an edit and a
        # re-check each time — even though the set is derivable from the work
        # type the engine already recorded (basicly-kjc5.44).
        return _blocked(
            ctx,
            f"definition of ready incomplete: {', '.join(dor.missing)}"
            f" — emit the required structure with `basicly policy scaffold"
            f" --type {ctx.state.issue_type}`",
        )
    if ctx.state.issue_type in _LEAF_TYPES:
        return _start_build_leaf(ctx)
    if not ctx.inputs.children:
        return _blocked(ctx, "decompose needs an agent-proposed child plan", needs_input="children")
    result = decompose.decompose(ctx.repo_root, ctx.issue_id, ctx.inputs.children)
    return _moved(
        ctx,
        "decompose",
        "decomposed",
        f"created {len(result.children)} children in {result.parallel_groups} group(s)"
        # A group count with no reason for it is where the collapse hid: the loop is
        # how decompose actually runs in the factory, and `basicly decompose`'s
        # report is a surface nobody reads on that path (basicly-jr0l.45).
        + decompose.collapse_note(result.collapsing),
    )


def _on_decompose(ctx: _Ctx) -> AdvanceResult:
    """Children exist: gate the decompose checkpoint, then fan out and land them."""
    if not policy.checkpoint_approved(ctx.repo_root, ctx.issue_id, "decompose"):
        return _blocked(ctx, "decompose checkpoint awaiting human approval", checkpoint="decompose")
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


def stale_binding_verdict(repo_root: Path, binding: loop_state.WorktreeBinding) -> tuple[bool, str]:
    """Whether a dead worktree *binding* may be cleared, and why.

    A binding is the only evidence that reaches the ``build`` rung of
    :func:`loop_state.derive_phase`, and it is *tracker* state while the worktree is
    *filesystem* state. When the worktree goes without the ref being cleared, the node
    derives ``build`` forever: the supervisor adopts it non-live, and both
    ``ready_lanes`` and the phase gate in ``advance_parked`` skip it — so it is
    simultaneously past classify and undispatchable (basicly-1koh).

    The verdict splits on whether work can be stranded, because that is what decides
    if clearing is safe. :func:`_worktree_landed` is the same deterministic proof the
    post-merge check uses: it holds when the branch is gone (``git branch -d`` refuses
    an unmerged branch) or when its tip is an ancestor of base. Then nothing can be
    lost and the ref may go. Otherwise commits may still be sitting on the branch, so
    clearing would orphan them and re-provisioning would fork a second branch for the
    same bead — this refuses and names the branch, per fail-closed-on-an-indeterminate-
    answer.

    A pure read: the caller does the clearing, so the decision and the write stay
    separable and this stays callable from a status command.
    """
    if _worktree_landed(repo_root, binding):
        return True, (
            f"worktree {binding.name!r} is gone and branch {binding.branch!r} holds "
            "nothing unlanded, so the stale binding can be cleared"
        )
    return False, (
        f"worktree {binding.name!r} is gone but branch {binding.branch!r} still holds "
        "unlanded commits; merge or delete that branch before the binding is cleared, "
        "or those commits become unreachable from the loop"
    )


def clear_worktree_binding(repo_root: Path, issue_id: str) -> None:
    """Drop *issue_id*'s ``worktree:`` external_ref, so it stops deriving ``build``."""
    _run_br(repo_root, ["update", issue_id, "--external-ref", ""])


def _on_verify(ctx: _Ctx) -> AdvanceResult:
    """Required gate is green (that is why we are here): gate the ship checkpoint."""
    if not policy.checkpoint_approved(ctx.repo_root, ctx.issue_id, "ship"):
        return _blocked(ctx, "ship checkpoint awaiting human approval", checkpoint="ship")
    return _moved(ctx, "ship", "shipped", "ship checkpoint satisfied")


def _worktree_landed(repo_root: Path, binding: loop_state.WorktreeBinding) -> bool:
    """True when the worktree branch has landed on its base (or is already gone).

    A branch that no longer exists was merged and cleaned — ``git branch -d``
    refuses an unmerged branch, so a missing branch is proof it landed. An
    existing branch counts as landed only when its tip is an ancestor of the base
    HEAD, i.e. ``_verify_and_land`` really ran ``merge.merge_worktree``. This is
    the deterministic signal ``_on_ship`` uses to refuse closing a stranded node.

    Shares :func:`merge.is_ancestor` with the landing's own post-merge proof
    (basicly-jr0l.46), so the two places that decide whether work landed cannot
    answer the question differently.
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
    return merge.is_ancestor(repo_root, branch, base)


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
    # Before the close, and before the tracker commit that flushes it: a rollup
    # written after that commit would sit in the local db only, and the whole point
    # of it is to travel with the clone (basicly-kjc5.50).
    rolled = _record_cost_rollup(ctx)
    _run_br(ctx.repo_root, ["close", ctx.issue_id, "--reason", "shipped by the harness loop"])
    committed = merge.commit_tracker_state(
        ctx.repo_root, ctx.issue_id, action="close the shipped track"
    )
    detail = "worktree torn down and issue closed"
    if rolled:
        detail += "; cost rollup recorded"
    if committed:
        detail += "; tracker state committed"
    else:
        # The ship itself succeeded — the code merged and the bead closed — so this
        # is a warning on a completed step, not a failure. What it must not be is
        # silent: an unrelated dirty file in base once made this skip the closing
        # chore(beads) commit with no hint at all, and the operator pushed the code
        # without the tracker state and found out later (basicly-f7li).
        detail += _skipped_tracker_suffix(ctx)
    return _moved(ctx, "done", "tore-down", detail)


def _record_cost_rollup(ctx: _Ctx) -> bool:
    """Write the shipped package's forecast-vs-actual cost onto its bead (kjc5.50).

    The history is machine-local otherwise: run-records live in the self-ignored
    ``.basicly/usage/``, so a fresh clone would forecast this package's class from
    the seed factors and never learn what the package actually cost. The bead is
    the only carrier that survives a clone, so the rollup goes there — the forecast
    that was made beside the actual it produced, summed over *every* dispatch
    including the failed ones, plus the counts behind it.

    A node that was never dispatched — a decomposed feature, whose cost is its
    children's packages — gets no rollup: it did not cost anything itself, and
    counting it as a landed package would both double-count the work and dilute
    cost-per-landed-package with a null.

    Best-effort in full: this runs after the merge, on a package that has shipped.
    Evidence is never worth failing a landing for, so any tracker or telemetry
    failure returns False and the ship proceeds.
    """
    try:
        history = run_record.dispatch_history(ctx.repo_root).get(ctx.issue_id, [])
        if not history:
            return False
        rework: int | None = None
        with contextlib.suppress(RuntimeError, ValueError, OSError):
            rework = policy.rework_recorded(ctx.repo_root, ctx.issue_id)
        info = decompose.bead_class_and_scope(ctx.repo_root, ctx.issue_id)
        task_class, scope = info if info is not None else (None, ())
        # Money is never recomputed — the forecast carries tokens only, from the
        # estimate the governor froze when it accepted this package's plan.
        estimate = (
            decompose.forecast_for(ctx.repo_root, task_class, scope)
            if task_class is not None
            else None
        )
        forecast = run_record.CostForecast(tokens=estimate.total if estimate else None)
        ident = run_record.record_cost_marker(
            ctx.repo_root,
            ctx.issue_id,
            actual=run_record.cost_rollup(history, rework=rework),
            forecast=forecast,
            task_class=task_class,
            scope_tokens=estimate.scope_tokens if estimate else None,
        )
    except RuntimeError, ValueError, OSError:
        return False
    return ident is not None


def _skipped_tracker_suffix(ctx: _Ctx) -> str:
    """`; <warning>` when foreign dirt blocked the tracker commit, else empty.

    Empty covers the ordinary case of nothing pending to commit, which needs no
    words — only a *declined* commit is news.
    """
    warning = merge.skipped_tracker_commit_warning(ctx.repo_root)
    return f"; {warning}" if warning else ""


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
    claimed = merge.commit_tracker_state(
        ctx.repo_root, ctx.issue_id, action="record the claim before provisioning"
    )
    name = _worktree_name(ctx.issue_id)
    session = worktree.create(name, base=wt_config.base_branch, repo_root=ctx.repo_root)
    _bind_worktree(ctx, name, session.branch)
    dispatched = _dispatch_runner(ctx, name, Path(session.worktree_path))
    if claimed:
        return dispatched
    # Same silence as the ship case, with a different cost: an unpublished claim is
    # invisible to a teammate pulling the repo, so two sessions can start the same
    # bead (basicly-f7li).
    suffix = _skipped_tracker_suffix(ctx)
    return replace(dispatched, detail=dispatched.detail + suffix) if suffix else dispatched


def _dispatch_runner(ctx: _Ctx, name: str, cwd: Path) -> AdvanceResult:
    """Run the selected agent headless in the worktree; a handoff just blocks.

    Cost-gated before anything spawns, because this was the one dispatch site with no
    gate on it at all (basicly-1th1). ``policy.spend_status`` is D3's single halt
    predicate, and its three enforcing call sites were delegated approval, the
    supervised lane admission, and decider delegation — an interactive ``loop run``
    reached ``runner.run`` past all three, so an exhausted grant still spent real money
    on the path a human is most likely to drive by hand.
    """
    refused = _dispatch_refused(ctx, name)
    if refused is not None:
        return refused
    dispatch = _run_agent(ctx, ctx.issue_id, cwd)
    if dispatch.result.handoff:
        return _blocked(ctx, f"worktree {name!r} provisioned; awaiting the agent's work")
    held = _runner_block(ctx, dispatch, issue_id=ctx.issue_id, target=f"worktree {name!r}")
    return held or _blocked(
        ctx,
        f"runner {dispatch.spec.name!r} finished in worktree {name!r}; advance again to land it",
    )


def _dispatch_refused(ctx: _Ctx, name: str) -> AdvanceResult | None:
    """Why this dispatch must not start, or None to go ahead (basicly-1th1).

    Both of the supervised path's forward-looking gates, applied to the interactive one:
    the D3 spend halt, and the working-set band. Inert without a ``grant_root`` — a
    caller that named no session has no grant ledger to read and no session to size
    against, which is exactly the behaviour every such caller already had.

    Reuses ``supervise``'s admission rather than re-deriving it. A second copy of a
    sizing rule is how the number that gates a dispatch and the number recorded beside
    its actual come to disagree, which is the defect basicly-jr0l.34 exists to prevent.
    That module imports this one, so the import is deferred to this call.

    A running dispatch is never interrupted — decision 14 — so this only ever declines
    to *start* one.
    """
    if ctx.grant_root is None:
        return None
    from . import supervise  # noqa: PLC0415 — supervise imports loop; deferred to break it

    spend = policy.spend_status(ctx.repo_root, ctx.grant_root)
    if spend.halted:
        return _blocked(
            ctx,
            f"dispatch refused before it started: {spend.detail}",
            needs_input="grant",
        )
    sizing = load_sizing_config(ctx.repo_root)
    admission = supervise.admit_working_set(ctx.repo_root, ctx.issue_id, sizing)
    queued = supervise.escalate_working_set(ctx.repo_root, admission)
    if not admission.refused:
        return None
    held = f"; held by {queued.decision_id}" if queued is not None else ""
    return _blocked(
        ctx,
        f"dispatch into worktree {name!r} refused before it started: {admission.violation}{held}",
        needs_input="scope",
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

    The sizing is measured *before* the agent runs and recorded with the dispatch
    (basicly-kjc5.30, basicly-jr0l.34). The scope read-cost is the denominator of
    every calibration sample, so measuring it later — against a tree this very
    dispatch is about to change — is what let the build factors drift; and the
    forecast has to be written here, beside the actual this same record will
    receive, or the forecast error is not computable at all.
    """
    config = load_runner_config(ctx.repo_root)
    spec = runner.select_runner(config.specs, config.default, capable=runner.is_capable)
    prompt = dispatch_prompt(issue_id)
    sizing = sizing_at_dispatch(ctx.repo_root, issue_id)
    # A sub-task runner is the lane's own write agent (D7), so it draws on the
    # lane reservation like any lane dispatch (component 8, basicly-kjc5.11).
    with runner.process_budget().slot(runner.LANE):
        result = runner.run(
            spec,
            prompt,
            cwd,
            capture_usage=True,
            timeout=config.runner_timeout,
        )
    record_run(
        ctx.repo_root,
        issue_id,
        spec,
        result,
        prompt=prompt,
        phase=run_record.BUILD_PHASE,
        **sizing,
    )
    return _Dispatch(spec=spec, result=result, cwd=cwd, timeout=config.runner_timeout)


def sizing_at_dispatch(repo_root: Path, issue_id: str) -> dict[str, object]:
    """The bead's sizing inputs as ``record_dispatch`` keywords, empty when unreadable.

    The keywords themselves come from :meth:`decompose.DispatchSizing.record_inputs`,
    which is what keeps this identical to the supervisor's lane dispatch — that one
    resolves the same sizing to *gate* on it (basicly-jr0l.16) and records the
    verdict's own numbers, so a forecast reaching only one of the two sites would
    leave exactly the expensive lane runs unpairable (basicly-jr0l.34).

    Telemetry on the critical path, so it never raises: a bead with no readable
    ``## Scope`` section records nothing and calibration falls back to measuring the
    tree, exactly as it did before.
    """
    with contextlib.suppress(RuntimeError, ValueError, OSError):
        sizing = decompose.dispatch_sizing(repo_root, issue_id)
        if sizing is not None:
            return sizing.record_inputs()
    return {}


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
    repo_root: Path,
    issue_id: str,
    spec: runner.RunnerSpec,
    result: runner.RunResult,
    **inputs: object,
) -> None:
    """Persist a metadata-only run-record for this dispatch, keyed by the bead.

    Thin alias for :func:`runner.record_dispatch`, which every dispatch site now
    shares (the loop, the supervisor, the rubric judge, and the decider) so all of
    them feed the one telemetry stream. *inputs* forwards the recorded dispatch
    inputs (prompt, phase, sizing, folded record ids) unchanged.
    """
    runner.record_dispatch(repo_root, issue_id, spec, result, **inputs)  # type: ignore[arg-type]


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


def _build_evidence_block(ctx: _Ctx, worktree_name: str) -> AdvanceResult | None:
    """The ``build`` phase's evidence precondition, resolved against its worktree.

    A build artifact is produced in the lane's own worktree, so it is checked
    there — the merge that would bring it into base is the very step this decides
    whether to run (basicly-m4zv.13). The session is read only when something is
    declared, so the default configuration pays nothing for this.
    """
    root = ctx.repo_root
    if ctx.config.evidence.get("build"):
        session = worktree.load_session(worktree_name, ctx.repo_root)
        if session is None:
            return _blocked(
                ctx,
                f"worktree {worktree_name!r} has no session record, so the declared "
                "build evidence artifact cannot be located; re-provision the worktree",
            )
        root = Path(session.worktree_path)
    return _evidence_block(ctx, root)


def _live_lane_scopes(ctx: _Ctx) -> dict[str, tuple[str, ...]]:
    """Declared scopes of the *other* beads that currently hold a worktree.

    A collision only means something against a lane that is still building: a bead
    whose worktree is gone has landed or been torn down, and nobody is about to
    write into its scope. So the live set is the worktree session records on disk —
    what ``worktree.create`` wrote — mapped back to beads through the same
    derivation the loop provisions with.

    The tracker export cannot serve as the index here: the ``worktree:`` binding is
    written with ``br update --external-ref`` and is not flushed to
    ``issues.jsonl`` until the next tracker commit, so a freshly provisioned lane —
    exactly the one most likely to be mid-edit — would be invisible. Bead *ids* are
    stable in the export, which is all this needs.
    """
    live = {session.name for session in worktree.list_sessions(ctx.repo_root)}
    if not live:
        return {}
    known = merge.known_bead_ids(ctx.repo_root) or set()
    lanes = sorted(bead for bead in known if bead != ctx.issue_id and _worktree_name(bead) in live)
    return merge.declared_scopes(ctx.repo_root, lanes)


def _scope_block(ctx: _Ctx, worktree_name: str) -> AdvanceResult | None:
    """Hold the lane's committed changes against its declared scope (basicly-jr0l.44).

    ``decompose`` treated the declared ``## Scope`` as a planning input and nothing
    ever checked it again, so a wrong or stale declaration was not detected when it
    was made — it surfaced later and indirectly, as a merge-queue conflict, by which
    point two lanes had already done work that fights. This is that check, at the
    one moment the lane's real diff exists: the build->verify landing, before the
    merge, so a refusal has spent nothing.

    Two outcomes, and only one of them is a refusal:

    - **Every** out-of-scope path is recorded on the bead as evidence, and that
      alone: a plan authored by an agent will sometimes be legitimately incomplete,
      and refusing each of those would convert it into a rework cycle that costs
      more than the finding is worth.
    - A path that also falls inside **another live lane's** declared scope is the
      case that actually causes the collision, and ``[policy] scope_collision``
      decides it deterministically: ``block`` refuses here, ``warn`` lands.

    Inert for a bead with no readable declared scope — a hand-filed leaf declares
    no plan and so contradicts none — which is also what keeps this from costing a
    tracker read on repos that never decompose.
    """
    declared = decompose.bead_class_and_scope(ctx.repo_root, ctx.issue_id)
    if declared is None or not declared[1]:
        return None
    session = worktree.load_session(worktree_name, ctx.repo_root)
    if session is None:
        # The landing itself refuses a missing session with a better message; this
        # check has nothing to compute and must not pre-empt it with a worse one.
        return None
    changed = merge.branch_changed_paths(ctx.repo_root, session.base, session.branch)
    outside = merge.out_of_scope_paths(changed, declared[1])
    if not outside:
        return None
    colliding = merge.coupled_lanes(outside, _live_lane_scopes(ctx), bounced=ctx.issue_id)
    policy.record_scope_violation(ctx.repo_root, ctx.issue_id, outside, colliding)
    if not colliding or ctx.config.scope_collision != "block":
        return None
    return _blocked(
        ctx,
        f"{ctx.issue_id} changed {', '.join(outside)} outside its declared scope "
        f"({', '.join(declared[1])}), and {', '.join(colliding)} declared that ground: "
        "the plan is wrong, not the merge. Widen this bead's '## Scope' when the work "
        "really belongs here, or move those edits to the lane that owns them, then "
        'advance again (set [policy] scope_collision = "warn" to land on the finding '
        "instead)",
        needs_input="scope",
    )


def _unreliable_landing_block(ctx: _Ctx, result: merge.MergeResult) -> AdvanceResult:
    """Record an unreliable landing gate and hold, escalating at the bound.

    The gate failed and then passed unchanged, so nothing here faults this work:
    record the flake and block for another landing attempt rather than spending the
    node's bounded budget on it (basicly-55yh).
    """
    events = policy.record_unreliable_gate(
        ctx.repo_root, ctx.issue_id, merge.MERGE_GATE, result.detail
    )
    # ...but "block and try again" with nothing counting the tries is a livelock
    # (basicly-jr0l.41). No budget is spent, so no cap is reached, so a chronically
    # unreliable gate defers its lane forever while looking merely slow. At the bound
    # the lane escalates to the same queue an exhausted budget uses, so a human sees
    # an untrustworthy gate rather than a lane that never finishes.
    if events >= policy.MAX_UNRELIABLE_GATE_EVENTS:
        question = policy.unreliable_gate_escalation_question(merge.MERGE_GATE)
        decisions.enqueue(
            ctx.repo_root,
            ctx.issue_id,
            policy.REWORK_ESCALATION_KIND,
            question,
            result.detail,
        )
        return _blocked(ctx, f"escalated: {question}", landing=result)
    return _blocked(ctx, result.detail, landing=result)


def _verify_and_land(
    ctx: _Ctx, worktree_name: str, *, verify_mode: str | None = None
) -> AdvanceResult:
    """Land the worktree (merge re-verifies internally), then record the required gate.

    Idempotent across an interruption: if a previous attempt merged and died before
    recording the gate, this resumes at the gate rather than re-reading the branch as
    empty (basicly-jr0l.50).

    The single funnel for the build->verify transition — both the plain leaf and a
    lane's ``_integrate_lane`` reach the landing through here — which is why the
    ``build`` phase's evidence check lives at the top of it rather than in
    :func:`advance` (basicly-m4zv.13). It runs before the merge, so a refusal has
    spent nothing. It also deliberately outranks the ``jr0l.50`` forward recovery:
    where the commits already sit does not change that the declared artifact is
    missing, and holding costs only the artifact, which is what was asked for.

    The declared-scope check (:func:`_scope_block`, basicly-jr0l.44) sits here for
    the same two reasons — one funnel, and before the merge — and after the
    evidence check, so a landing missing both is held on the cheaper one first.
    """
    for precondition in (_build_evidence_block, _scope_block):
        held = precondition(ctx, worktree_name)
        if held is not None:
            return held
    mode = verify_mode or ctx.inputs.verify_mode
    result = merge.merge_worktree(ctx.repo_root, worktree_name, bead=ctx.issue_id, verify_mode=mode)
    if result.status == merge.ALREADY_LANDED:
        # The merge already happened and the process died before the gate record.
        # Finish the landing forward: re-merging is impossible and re-running the
        # build is wrong, because the work is already in base (basicly-jr0l.50).
        return _record_verify(ctx, result.detail, verify_mode=mode)
    if result.status == "not-ready":
        # The build's work is not committed on the branch: block with guidance,
        # do not burn a rework attempt on an operator-fixable state (basicly-4psl).
        return _blocked(ctx, result.detail, landing=result)
    if result.unreliable:
        return _unreliable_landing_block(ctx, result)
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
    gate_error = _record_gate(ctx, subtask_id, report)
    if gate_error is not None:
        return _blocked(ctx, f"{where}: {gate_error}")
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

    D4: validate is acceptance-criteria satisfaction. It is a **composite of two
    gates with different types**, recorded separately by
    :func:`rubrics.report_gate` (gates-and-rework-design.md §4.1):

    - ``rubric`` — the **pre-flight** half. Deterministic checks only, promoted
      from advisory to **required** at lane level. The promotion belongs to the
      level, not to ``[policy] required_gates``, so a consumer's gate list cannot
      silently drop it.
    - ``rubric-judged`` — the **escalation** half. Judged checks only, never
      required, so it can record an honest ``fail`` without killing the lane.

    Splitting them is what gives the required half teeth: as one gate, D4 promoted
    to required a gate whose judged checks could not fail it, so it could pass
    having checked nothing. A work class no rubric covers still has nothing to
    validate.

    Two failure shapes, deliberately different (D4 as amended 2026-07-25), and now
    each with a gate type behind it rather than a special case here:

    - a **deterministic** no is a test failure — spend a bounded rework attempt;
    - a **judged** no is a *decision* — enqueue it with its evidence and hold the
      lane. It does not land, does not bounce, and does not spend a rework
      attempt, because a false NO from a model must not consume the budget that
      exists for real defects. A human, or the decider under an L2+ grant,
      disposes of it.
    """
    selected = rubrics.select_rubrics(rubrics.load_rubrics(), ctx.state.issue_type)
    if not selected:
        return None
    verdicts = [
        verdict for rubric in selected for verdict in rubrics.evaluate(ctx.issue_id, rubric, cwd)
    ]
    rubrics.report_gate(ctx.repo_root, ctx.issue_id, verdicts)
    if rubrics.gate_status(verdicts) == "fail":
        failed = ", ".join(
            verdict.check_id
            for verdict in verdicts
            if verdict.kind == rubrics.DETERMINISTIC and verdict.answer == rubrics.NO
        )
        return _rework(ctx, rubrics.RUBRIC_GATE, f"lane validate failed: {failed}")
    disputed = rubrics.judged_failures(verdicts)
    if disputed:
        return _hold_for_validate_decision(ctx, disputed)
    return None


def _hold_for_validate_decision(ctx: _Ctx, disputed: list[rubrics.CheckVerdict]) -> AdvanceResult:
    """Enqueue the disputed acceptance criteria and hold the lane (D4 amended, R4).

    The item carries the failing criterion ids and the validator's evidence, so
    whoever disposes of it can see what was claimed and on what basis without
    re-reading the lane. ``enqueue`` is idempotent per (issue, kind, question), so
    re-advancing a held lane re-reports the same item instead of flooding.
    """
    criteria = ", ".join(verdict.check_id for verdict in disputed)
    evidence = "; ".join(f"{verdict.check_id}: {verdict.evidence}" for verdict in disputed)
    decisions.enqueue(
        ctx.repo_root,
        ctx.issue_id,
        "validate",
        f"acceptance criteria unmet per the validator ({criteria}): accept, rework, or amend?",
        evidence,
    )
    return _blocked(
        ctx,
        f"lane validate disputed: {criteria} — queued as a decision, lane holds "
        "(dispose of it with `basicly loop answer`)",
        # "decision", not "held": a held lane is *carried* and landed dispatch-less
        # on the next supervisor pass (kjc5.18), which would defeat the hold. The
        # "decision" route is outside RETRIABLE_ROUTES, so the lane waits.
        action="decision",
    )


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
    gate_error = _record_gate(ctx, ctx.issue_id, report)
    if gate_error is not None:
        return _blocked(ctx, gate_error)
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
            policy.REWORK_ESCALATION_KIND,
            policy.rework_escalation_question(gate),
            reason,
        )
    return _blocked(
        ctx,
        f"{reason} (rework {attempts}/{ctx.config.max_rework})",
        action=action,
        landing=landing,
    )


def _ensure_child_worktrees(ctx: _Ctx, children: list[tuple[str, str]]) -> None:
    """Provision worktrees for the highest-ranked dispatchable children, up to the cap.

    Ordered by ``br scheduler`` rank rather than by the order br happens to return
    the dependents in. The cap makes provisioning a *selection*: whichever children
    it reaches are the pass, and :func:`supervise.ready_lanes` can only rank-order
    the set chosen here. Reducing ``ready_ranked`` to a membership set therefore
    discarded the one ordering that decides which work actually runs — a computed
    ranking thrown away, so an arbitrary br ordering picked the lanes
    (basicly-jr0l.62).

    A child the band refuses is skipped rather than provisioned. Dispatch drops it
    regardless — :func:`supervise.escalate_working_set` leaves a pending decision
    and ``ready_lanes`` filters on that — so provisioning it spends a concurrency
    slot on a worktree nothing ever runs in, and crowds out an admissible lane.

    An *unsizeable* child is still provisioned. An unreadable scope is not a
    refusal (``admit_working_set`` sets ``refused`` on the ceiling alone), and
    dropping it here would silently lose work rather than defer it.
    """
    from . import supervise  # noqa: PLC0415 — supervise imports loop; deferred to break it

    wt_config = load_worktree_config(ctx.repo_root)
    sizing = load_sizing_config(ctx.repo_root)
    existing = {session.name for session in worktree.list_sessions(ctx.repo_root)}
    room = wt_config.concurrency - len(existing)
    open_children = {cid for cid, status in children if status != "closed"}
    ranked = [
        node.issue_id
        for node in loop_state.ready_ranked(ctx.repo_root)
        if node.issue_id in open_children
    ]
    # Publish the fan-out claims the same way a leaf publishes its own.
    merge.commit_tracker_state(
        ctx.repo_root, ctx.issue_id, action="record the claim before provisioning"
    )
    for cid in ranked:
        if room <= 0:
            break
        name = _worktree_name(cid)
        if name in existing:
            continue
        if supervise.admit_working_set(ctx.repo_root, cid, sizing).refused:
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
    grant_root: str | None = None,
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

    ctx = _Ctx(repo_root, issue_id, state, config, inputs, grant_root)
    if state.phase in _BASE_CHECKOUT_PHASES and worktree.is_linked_checkout(repo_root):
        return _blocked(
            ctx,
            f"the {state.phase!r} transition merges/ships and must run from the base "
            f"checkout, not a linked worktree ({repo_root}); cd to the base checkout "
            "and re-run 'basicly loop advance'",
            needs_input="base-checkout",
        )
    # Evidence is a precondition on *leaving* a phase, so it is checked before the
    # handler runs and can spend nothing (basicly-m4zv.13). ``build`` is the one
    # phase whose handler also takes steps that stay inside it — a lane runs its
    # sub-tasks through ``_on_build`` — and those steps are what produce a build
    # artifact in the first place, so checking here would deadlock the lane on its
    # own evidence. Its check sits at the single funnel for the build->verify
    # transition instead (``_verify_and_land``).
    if state.phase != "build":
        held = _evidence_block(ctx)
        if held is not None:
            return held
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


# --- The whole-boundary ceremony (basicly-kjc5.41, design D10) ---------------
#
# :func:`run_until_blocked` stops dead at a checkpoint, and the checkpoint
# cannot be answered from inside it — so an agent driving one leaf bead had to
# interleave `policy dor`, `policy checkpoint --approve` (twice: mint, then
# confirm) and `loop advance` by hand, about six deterministic commands per
# bead. D10 says a deterministic *sequence* an agent performs by hand means the
# engine is missing a command. :func:`run_ceremony` is that command's engine
# half: it drives the loop across a whole phase boundary, resolving each
# checkpoint it is authorized to resolve and stopping cleanly on the ones it is
# not. It never widens authorization — resolution goes through
# :func:`policy.approve_checkpoint_guarded`, so a TTY, a covering autonomy
# grant, or a relayed one-time code are still the only three ways in.


@dataclass(frozen=True)
class CheckpointApproval:
    """A human checkpoint the ceremony resolved itself, between two loop steps."""

    checkpoint: str
    detail: str = ""


# One ceremony event, in the order it happened: a loop step, or the checkpoint
# approval that unblocked the next one.
CeremonyEvent = AdvanceResult | CheckpointApproval


@dataclass(frozen=True)
class CeremonyResult:
    """The outcome of one :func:`run_ceremony` call."""

    events: tuple[CeremonyEvent, ...] = ()
    # The checkpoint that challenged, with the one-time code a human must relay
    # back. Set only when the ceremony stopped for want of authorization.
    challenge: tuple[str, str] | None = None
    # Why a grant did not resolve that challenge itself, when one existed and
    # declined it (basicly-5ltn). Empty when no grant was consulted, so the
    # ungranted challenge stays as bare as it was.
    challenge_reason: str = ""
    # The checkpoint that refused, with why — a bad or expired code, or a grant
    # precondition that does not hold.
    refused: tuple[str, str] | None = None

    @property
    def steps(self) -> tuple[AdvanceResult, ...]:
        """Just the loop steps, dropping the approvals the ceremony interleaved."""
        return tuple(event for event in self.events if isinstance(event, AdvanceResult))

    @property
    def approvals(self) -> tuple[CheckpointApproval, ...]:
        """Just the checkpoints this call resolved."""
        return tuple(event for event in self.events if isinstance(event, CheckpointApproval))

    @property
    def blocked(self) -> bool:
        """True when the ceremony stopped short of shipping the track.

        Waiting on the agent's work is the *expected* end of the opening
        boundary, so this is not "something went wrong" — it means the track
        needs a human or an agent before the loop moves again.
        """
        steps = self.steps
        if self.challenge is not None or self.refused is not None:
            return True
        return not steps or steps[-1].to_phase != "done"


def run_ceremony(  # noqa: PLR0913 — mirrors the CLI surface
    repo_root: Path,
    issue_id: str,
    *,
    config: PolicyConfig | None = None,
    inputs: Inputs | None = None,
    interactive: bool = False,
    confirms: Mapping[str, str] | None = None,
    grant_root: str | None = None,
    max_steps: int = 20,
) -> CeremonyResult:
    """Advance across a whole phase boundary, resolving the checkpoints it may.

    Drives :func:`advance` like :func:`run_until_blocked`, except that a block on
    a human checkpoint is not the end: the checkpoint is put through
    :func:`policy.approve_checkpoint_guarded` (TTY, a covering grant on
    *grant_root*, or a matching one-time code from *confirms*) and, when that
    approves, the loop keeps going. Stops on a challenge or a refusal — carrying
    the code to relay (plus why a grant declined to resolve it, when one did), or
    the reason — and on any block that is not a checkpoint:
    a missing input, a red gate, or the handoff that awaits the agent's work.

    Never mints more than one challenge per call: a challenged checkpoint blocks
    the loop, so there is nothing further to drive until a human answers it.
    """
    config = config or load_policy_config(repo_root)
    inputs = inputs or Inputs()
    codes = dict(confirms or {})
    events: list[CeremonyEvent] = []
    resolved: set[str] = set()
    for _ in range(max_steps):
        # The *grant_root* the checkpoints below are approved against also gates the
        # dispatch this may approve its way into (basicly-1th1): a ceremony that can
        # reach a build must read the ledger that build spends under.
        result = advance(repo_root, issue_id, config=config, inputs=inputs, grant_root=grant_root)
        events.append(result)
        if result.to_phase == "done":
            break
        if not result.blocked:
            continue  # real progress — keep driving toward the boundary
        name = result.checkpoint
        # Not a checkpoint block, or one this call already approved (which would
        # spin): the boundary ends here.
        if name is None or name in resolved:
            break
        approval = policy.approve_checkpoint_guarded(
            repo_root,
            issue_id,
            name,
            interactive=interactive,
            confirm=codes.pop(name, None),
            grant_root=grant_root,
        )
        if approval.status == "challenge":
            return CeremonyResult(
                tuple(events),
                challenge=(name, approval.code or ""),
                challenge_reason=approval.detail,
            )
        if approval.status != "approved":
            return CeremonyResult(tuple(events), refused=(name, approval.detail))
        resolved.add(name)
        events.append(CheckpointApproval(name, approval.detail))
    return CeremonyResult(tuple(events))
