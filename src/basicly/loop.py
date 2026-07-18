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
"""

from __future__ import annotations

import contextlib
import json
from dataclasses import dataclass
from pathlib import Path

from . import classify, decompose, loop_state, merge, policy, run_record, runner, verify, worktree
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
    # |"done"|"blocked"|"escalated"
    action: str
    detail: str = ""
    needs_input: str | None = None

    @property
    def advanced(self) -> bool:
        """True when the track moved to a new phase."""
        return self.to_phase != self.from_phase

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
    ctx: _Ctx, reason: str, *, action: str = "blocked", needs_input: str | None = None
) -> AdvanceResult:
    return AdvanceResult(
        ctx.issue_id, ctx.state.phase, ctx.state.phase, action, reason, needs_input
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
    """A leaf worktree is bound: verify and land it (rework on failure)."""
    if ctx.state.worktree is None:
        return _blocked(ctx, "build phase without a bound worktree")
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
        worktree.cleanup(binding.name, force=False)
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
    active = len(worktree.list_sessions())
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
    session = worktree.create(name, base=wt_config.base_branch)
    _bind_worktree(ctx, name, session.branch)
    return _dispatch_runner(ctx, name, Path(session.worktree_path))


def _dispatch_runner(ctx: _Ctx, name: str, cwd: Path) -> AdvanceResult:
    """Run the selected agent headless in the worktree; a handoff just blocks."""
    config = load_runner_config(ctx.repo_root)
    spec = runner.select_runner(config.specs, config.default, capable=runner.is_capable)
    result = runner.run(spec, _dispatch_prompt(ctx.issue_id), cwd)
    _record_run(ctx, spec, result)
    if result.handoff:
        return _blocked(ctx, f"worktree {name!r} provisioned; awaiting the agent's work")
    if result.returncode != 0:
        tail = (result.stderr or result.stdout).strip().splitlines()
        detail = tail[-1] if tail else "no output"
        return _blocked(
            ctx,
            f"runner {spec.name!r} failed in worktree {name!r} "
            f"(exit {result.returncode}): {detail}",
        )
    return _blocked(
        ctx, f"runner {spec.name!r} finished in worktree {name!r}; advance again to land it"
    )


def _record_run(ctx: _Ctx, spec: runner.RunnerSpec, result: runner.RunResult) -> None:
    """Persist a metadata-only run-record for this dispatch, keyed by the bead.

    The command is redacted here (the prompt argument elided) before it is
    handed to the record, so no prompt or secret is ever persisted. Best-effort:
    a write failure must not fail the loop landing (same stance as the
    ``tool-usage`` telemetry hook), so an OS error is tolerated, not fatal.
    """
    command: tuple[str, ...] = ()
    if not result.handoff:
        command = tuple(runner.format_command(spec, run_record.REDACTED_PROMPT))
    entry = run_record.build_record(
        agent=spec.name,
        handoff=result.handoff,
        returncode=result.returncode,
        duration_s=result.duration_s,
        command=command,
        model=spec.model,
    )
    with contextlib.suppress(OSError):
        run_record.record(ctx.repo_root, ctx.issue_id, entry)


def _dispatch_prompt(issue_id: str) -> str:
    """The agent-neutral dispatch prompt: point at the tracker, not at an agent."""
    return (
        f"You are in a git worktree dedicated to the tracked issue {issue_id}. "
        f"Read AGENTS.md for the repo rules, run `br show {issue_id}` for the "
        "requirement and acceptance criteria, implement the work, and commit it "
        "on the current branch referencing that issue id. Do not merge, push, or "
        "close the issue — the harness loop lands and ships it."
    )


def _verify_and_land(ctx: _Ctx, worktree_name: str) -> AdvanceResult:
    """Land the worktree (merge re-verifies internally), then record the required gate."""
    result = merge.merge_worktree(
        ctx.repo_root, worktree_name, bead=ctx.issue_id, verify_mode=ctx.inputs.verify_mode
    )
    if not result.merged:
        return _rework(ctx, merge.MERGE_GATE, f"merge failed: {result.detail}")
    return _record_verify(ctx, result.detail)


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
    live = {session.name for session in worktree.list_sessions()}
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


def _record_verify(ctx: _Ctx, detail: str) -> AdvanceResult:
    """Run verify + record the required gate so the derived phase becomes verify."""
    report = verify.run_verify(ctx.repo_root, ctx.inputs.verify_mode)
    record = run_record.latest_record(ctx.repo_root, ctx.issue_id)
    verify.report_gate(ctx.repo_root, ctx.issue_id, report, actor=record.agent if record else None)
    if not report.passed:
        return _rework(ctx, verify.DEFAULT_GATE, f"verify failed: {', '.join(report.failures)}")
    return _moved(ctx, "verify", "merged", detail)


def _rework(ctx: _Ctx, gate: str, reason: str) -> AdvanceResult:
    """Record a rework attempt for *gate* and block, escalating at the cap."""
    attempts = policy.record_rework(ctx.repo_root, ctx.issue_id, gate)
    action = "escalated" if attempts >= ctx.config.max_rework else "blocked"
    return _blocked(ctx, f"{reason} (rework {attempts}/{ctx.config.max_rework})", action=action)


def _ensure_child_worktrees(ctx: _Ctx, children: list[tuple[str, str]]) -> None:
    """Provision a worktree for each dependency-unblocked, still-open child, up to the cap."""
    wt_config = load_worktree_config(ctx.repo_root)
    existing = {session.name for session in worktree.list_sessions()}
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
        session = worktree.create(name, base=wt_config.base_branch)
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
    human/agent then resolves the block and re-invokes.
    """
    results: list[AdvanceResult] = []
    for _ in range(max_steps):
        result = advance(repo_root, issue_id, config=config, inputs=inputs)
        results.append(result)
        if result.blocked or result.to_phase == "done":
            break
    return results
