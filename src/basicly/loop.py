from __future__ import annotations

import contextlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

from . import (
    classify,
    commit,
    context_meter,
    cost_rollup,
    curate,
    decisions,
    decompose,
    demonstration_proof,
    dispatch_brief,
    handoff,
    invest,
    landing_gate,
    lens_review,
    loop_state,
    merge,
    needs_input,
    plan_entry,
    policy,
    repair_brief,
    retrospective,
    roles,
    rubrics,
    run_record,
    runner,
    tracker,
    validate_gate,
    verify,
    working_set,
    worktree,
)
from .config import (
    WORK_TYPES,
    PolicyConfig,
    lane_scope,
    load_policy_config,
    load_runner_config,
    load_sizing_config,
    load_worktree_config,
)
from .dispatch_brief import child_plan_prompt, dispatch_prompt, work_type_prompt
from .tracker import try_add_comment as _add_comment
from .tracker import write as _write

if TYPE_CHECKING:
    from .decompose import ChildSpec

_LEAF_TYPES = ("bug", "chore", "task")

_BASE_CHECKOUT_PHASES = ("build", "ship")

_SUBTASK_VERIFY_MODE = "fast"
_LANE_VERIFY_MODE = "full"


@dataclass(frozen=True)
class Inputs:
    work_type: str | None = None
    children: tuple[ChildSpec, ...] | None = None
    verify_mode: str = "full"


@dataclass(frozen=True)
class AdvanceResult:
    issue_id: str
    from_phase: str
    to_phase: str
    action: str
    detail: str = ""
    needs_input: str | None = None
    landing: merge.MergeResult | None = None
    checkpoint: str | None = None

    @property
    def advanced(self) -> bool:
        return self.to_phase != self.from_phase

    @property
    def progressed(self) -> bool:

        return self.advanced or self.action == "sub-task"

    @property
    def blocked(self) -> bool:

        return self.action in ("blocked", "escalated", "decision")


@dataclass(frozen=True)
class _Ctx:
    repo_root: Path
    issue_id: str
    state: loop_state.NodeState
    config: PolicyConfig
    inputs: Inputs
    grant_root: str | None = None
    repair_dispatch: bool = True


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

    status = policy.evidence_status(root or ctx.repo_root, ctx.config, ctx.state.phase)
    if not status.satisfied:
        return _blocked(ctx, status.reason, needs_input="evidence")
    if status.declared is not None:
        policy.record_evidence(ctx.repo_root, ctx.issue_id, ctx.state.phase, status.declared)
    return None


def _record_gate(ctx: _Ctx, issue_id: str, report: verify.VerifyReport) -> str | None:

    record = run_record.latest_record(ctx.repo_root, issue_id)
    ok, message = verify.report_gate(
        ctx.repo_root, issue_id, report, actor=record.agent if record else None
    )
    return None if ok else f"verify gate not recorded on {issue_id}: {message}"


def _declared_scope(ctx: _Ctx) -> tuple[str, ...]:

    info = decompose.bead_class_and_scope(ctx.repo_root, ctx.issue_id)
    return info[1] if info is not None else ()


def _on_intake(ctx: _Ctx) -> AdvanceResult:

    work_type, attributed = ctx.inputs.work_type, ""
    if not work_type:
        proposal = _proposed_work_type(ctx)
        if proposal.work_type is None:
            return _blocked(
                ctx,
                _proposal_declined("classify needs an agent-proposed work type", proposal),
                needs_input="work_type",
            )
        work_type, attributed = proposal.work_type, f" ({proposal.by})"
    result = classify.classify(ctx.repo_root, ctx.issue_id, work_type, _declared_scope(ctx))
    return _blocked(
        ctx,
        f"recorded work type {result.work_type!r}{attributed}; "
        "classify checkpoint awaiting approval",
        checkpoint="classify",
    )


def _on_classify(ctx: _Ctx) -> AdvanceResult:

    dor = policy.definition_of_ready(ctx.repo_root, ctx.issue_id)
    if not dor.ready:
        voices = f". {invest.trigger_remedy()}" if invest.TRIGGER_HEADING in dor.missing else ""
        return _blocked(
            ctx,
            f"definition of ready incomplete: {', '.join(dor.missing)}"
            f" — emit the required structure with `basicly policy scaffold"
            f" --type {ctx.state.issue_type}`{voices}",
        )
    if ctx.state.issue_type in _LEAF_TYPES:
        return _start_build_leaf(ctx)
    children, attributed = ctx.inputs.children, ""
    if not children:
        proposal = _proposed_children(ctx)
        if proposal.children is None:
            return _blocked(
                ctx,
                _proposal_declined("decompose needs an agent-proposed child plan", proposal),
                needs_input="children",
            )
        children, attributed = proposal.children, f" ({proposal.by})"
    result = decompose.decompose(ctx.repo_root, ctx.issue_id, children)
    return _moved(
        ctx,
        "decompose",
        "decomposed",
        f"created {len(result.children)} children in {result.parallel_groups} group(s)"
        + attributed
        + decompose.collapse_note(result.collapsing)
        + demonstration_proof.plan_notice(ctx.repo_root, children),
    )


def _on_decompose(ctx: _Ctx) -> AdvanceResult:

    if not policy.checkpoint_approved(ctx.repo_root, ctx.issue_id, "decompose"):
        return _blocked(ctx, "decompose checkpoint awaiting human approval", checkpoint="decompose")
    plan = handoff.entry_verdict(ctx.repo_root, ctx.issue_id, handoff.IMPLEMENTATION_PLAN)
    if not plan.admitted:
        return _blocked(ctx, plan.reason, needs_input="artifact")
    return _build_children(ctx)


def _on_build(ctx: _Ctx) -> AdvanceResult:

    if ctx.state.worktree is None:
        return _blocked(ctx, "build phase without a bound worktree")
    if ctx.inputs.children and not ctx.state.has_children:
        return _decompose_lane(ctx, ctx.inputs.children)
    repaired = _repair_in_place(ctx, ctx.state.worktree)
    if repaired is not None:
        return repaired
    if ctx.state.has_children:
        return _run_lane(ctx, ctx.state.worktree)
    return _verify_and_land(ctx, ctx.state.worktree.name)


def stale_binding_verdict(repo_root: Path, binding: loop_state.WorktreeBinding) -> tuple[bool, str]:

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
    _write(repo_root, ["update", issue_id, "--external-ref", ""])


def _on_verify(ctx: _Ctx) -> AdvanceResult:

    approval = policy.approve_checkpoint_guarded(
        ctx.repo_root, ctx.issue_id, "ship", interactive=False, grant_root=ctx.grant_root
    )
    if approval.status != "approved":
        detail = "; ".join(
            part for part in ("ship checkpoint awaiting human approval", approval.detail) if part
        )
        return _blocked(ctx, detail, checkpoint="ship")
    return _moved(ctx, "ship", "shipped", "ship checkpoint satisfied")


def _on_validate(ctx: _Ctx) -> AdvanceResult:

    gate = validate_gate.VALIDATE_GATE
    if gate in ctx.state.gates.required_failed:
        repaired = _repair_from_validate(ctx, gate)
        if repaired is not None:
            return repaired
        return _rework(ctx, gate, f"{gate} failed: the change did not survive validation")
    dispatched = _dispatch_validation(ctx, gate)
    return (
        dispatched
        if dispatched is not None
        else _blocked(ctx, validate_gate.refusal_reason(ctx.state.gates), needs_input="validation")
    )


def _repair_from_validate(ctx: _Ctx, gate: str) -> AdvanceResult | None:

    binding = ctx.state.worktree
    if binding is None:
        return None
    if _worktree_landed(ctx.repo_root, binding):
        return _repair_in_place(ctx, binding)
    mode = ctx.inputs.verify_mode
    changed = _changed_paths(ctx, binding.name)
    landed = merge.merge_worktree(ctx.repo_root, binding.name, bead=ctx.issue_id, verify_mode=mode)
    if not landed.merged:
        return _rework(
            ctx,
            merge.MERGE_GATE,
            f"re-landing the repair failed: {landed.detail}",
            landing=landed,
            findings=_landing_findings(landed),
            evidence=_landing_evidence(landed, mode),
        )
    held = _record_change_summary(ctx, changed, landed)
    if held is not None:
        return held
    revalidated = _dispatch_validation(ctx, gate)
    if revalidated is not None:
        return revalidated
    return _blocked(ctx, f"the repair landed; {gate} was not re-run", needs_input="validation")


def _dispatch_validation(ctx: _Ctx, gate: str) -> AdvanceResult | None:

    if not ctx.repair_dispatch or validate_gate.has_foreign_result(ctx.state.gates):
        return None
    dispatch = _run_agent(
        ctx,
        ctx.issue_id,
        ctx.repo_root,
        prompt=dispatch_brief.validate_prompt(ctx.issue_id),
        phase="validate",
    )
    held = _runner_block(ctx, dispatch, issue_id=ctx.issue_id, target="the merged checkout")
    if held is not None:
        return held
    reply = runner.result_text(dispatch.spec, dispatch.result.stdout)
    verdict = validate_gate.verdict_from_reply(reply)
    if verdict is not None:
        validate_gate.record_verdict(ctx.repo_root, ctx.issue_id, passed=verdict)
    _dispatch_reviews(ctx)
    if verdict is None:
        return _blocked(
            ctx,
            validate_gate.queue_unreadable_verdict(
                ctx.repo_root, ctx.issue_id, repair_brief.clip_output(reply)
            ),
            action="decision",
            needs_input="validation",
        )
    if gate in policy.gate_status(ctx.repo_root, ctx.issue_id, ctx.config).required_passed:
        return _moved(ctx, "verify", "validated", f"{gate} recorded green by the validator")
    return _blocked(
        ctx,
        f"the validator recorded {gate} failed; the unit stays in validate",
        needs_input="validation",
    )


def _dispatch_reviews(ctx: _Ctx) -> None:

    for dispatch in roles.lens_dispatches("validate"):
        run = _run_agent(
            ctx,
            ctx.issue_id,
            ctx.repo_root,
            prompt=dispatch_brief.review_prompt(ctx.issue_id, dispatch.lens),
            phase="validate",
            role=dispatch.role,
        )
        lens_review.record(
            ctx.repo_root,
            ctx.issue_id,
            dispatch.lens,
            repair_brief.clip_output(runner.result_text(run.spec, run.result.stdout)),
        )


def _worktree_landed(repo_root: Path, binding: loop_state.WorktreeBinding) -> bool:

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

    unrun = demonstration_proof.unrun_reason(ctx.repo_root, ctx.issue_id)
    if unrun:
        return _blocked(ctx, unrun, needs_input="demonstration")
    binding = ctx.state.worktree
    if binding is not None:
        if not _worktree_landed(ctx.repo_root, binding):
            return _blocked(
                ctx,
                f"ship refuses to close: worktree branch {binding.branch!r} is not merged "
                "into its base — the build->verify landing was skipped (was the verify gate "
                "recorded out-of-band?); re-run the build->verify advance to land it first",
            )
        worktree.cleanup(binding.name, force=False, repo_root=ctx.repo_root, missing_ok=True)
    curated = _dispatch_curation(ctx)
    rolled = cost_rollup.record(ctx.repo_root, ctx.issue_id)
    _write(ctx.repo_root, ["close", ctx.issue_id, "--reason", "shipped by the harness loop"])
    committed = merge.commit_tracker_state(
        ctx.repo_root, ctx.issue_id, action="close the shipped track"
    )
    detail = "worktree torn down and issue closed"
    if rolled:
        detail += "; cost rollup recorded"
    detail += curated
    if committed:
        detail += "; tracker state committed"
    else:
        detail += _skipped_tracker_suffix(ctx)
    return _moved(ctx, "done", "tore-down", detail)


def _dispatch_curation(ctx: _Ctx) -> str:

    if not ctx.repair_dispatch or not handoff.adopted(ctx.repo_root, handoff.RELEASE_RECORD):
        return ""
    run = _run_agent(
        ctx,
        ctx.issue_id,
        ctx.repo_root,
        prompt=dispatch_brief.curate_prompt(ctx.issue_id),
        phase="ship",
    )
    said = curate.record(
        ctx.repo_root, ctx.issue_id, runner.result_text(run.spec, run.result.stdout)
    )
    return f"; {said}" if said else ""


def _skipped_tracker_suffix(ctx: _Ctx) -> str:

    warning = merge.skipped_tracker_commit_warning(ctx.repo_root)
    return f"; {warning}" if warning else ""


def _start_build_leaf(ctx: _Ctx) -> AdvanceResult:

    wt_config = load_worktree_config(ctx.repo_root)
    refusal = worktree.cap_refusal(wt_config.concurrency, ctx.repo_root)
    if refusal:
        return _blocked(ctx, refusal)
    claimed = merge.commit_tracker_state(
        ctx.repo_root, ctx.issue_id, action="record the claim before provisioning"
    )
    name = _worktree_name(ctx.issue_id)
    session = worktree.create(name, base=wt_config.base_branch, repo_root=ctx.repo_root)
    _bind_worktree(ctx, name, session.branch)
    dispatched = _dispatch_runner(ctx, name, Path(session.worktree_path))
    if claimed:
        return dispatched
    suffix = _skipped_tracker_suffix(ctx)
    return replace(dispatched, detail=dispatched.detail + suffix) if suffix else dispatched


def _dispatch_runner(ctx: _Ctx, name: str, cwd: Path) -> AdvanceResult:

    refused = _dispatch_refused(ctx, name)
    if refused is not None:
        return refused
    dispatch = _run_agent(ctx, ctx.issue_id, cwd)
    if dispatch.result.handoff:
        return _blocked(ctx, f"worktree {name!r} provisioned; awaiting the agent's work")
    held = _runner_block(ctx, dispatch, issue_id=ctx.issue_id, target=f"worktree {name!r}")
    if held is not None:
        return held
    return _blocked(
        ctx,
        f"runner {dispatch.spec.name!r} finished in worktree {name!r}"
        f"{_observe_context_ceiling(ctx, dispatch)}; advance again to land it",
    )


def _observe_context_ceiling(ctx: _Ctx, dispatch: _Dispatch) -> str:

    verdict = context_meter.meter_context_ceiling(
        dispatch.spec, dispatch.result, load_sizing_config(ctx.repo_root)
    )
    return f"; {verdict.observation}" if verdict.overrun else ""


def _dispatch_refused(ctx: _Ctx, name: str) -> AdvanceResult | None:

    if ctx.grant_root is None:
        return None
    entry = plan_entry.build_entry_verdict(ctx.repo_root, ctx.issue_id)
    if not entry.admitted:
        return _blocked(ctx, entry.reason, needs_input="plan")
    sizing = load_sizing_config(ctx.repo_root)
    admission = working_set.admit_working_set(ctx.repo_root, ctx.issue_id, sizing)
    queued = working_set.escalate_working_set(ctx.repo_root, admission)
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
    spec: runner.RunnerSpec
    result: runner.RunResult
    cwd: Path
    timeout: float


def _run_agent(  # noqa: PLR0913 — one keyword per independent fact about the dispatch
    ctx: _Ctx,
    issue_id: str,
    cwd: Path,
    *,
    prompt: str | None = None,
    phase: str = "build",
    role: str | None = None,
) -> _Dispatch:

    config = load_runner_config(ctx.repo_root)
    spec = runner.select_runner(config.specs, config.default, capable=runner.is_capable)
    prompt = prompt if prompt is not None else dispatch_prompt(issue_id)
    sizing = sizing_at_dispatch(ctx.repo_root, issue_id)
    role = (
        roles.resolve_role(ctx.repo_root, spec, phase)
        if role is None
        else roles.resolve_named_role(ctx.repo_root, spec, role)
    )
    prompt = _with_role_skills(ctx, spec, role, prompt, phase)
    with runner.process_budget().slot(runner.LANE):
        result = runner.run(
            spec,
            prompt,
            cwd,
            capture_usage=True,
            timeout=config.runner_timeout,
            role=role,
        )
    record_run(
        ctx.repo_root,
        issue_id,
        spec,
        result,
        prompt=prompt,
        phase=phase,
        **sizing,
    )
    return _Dispatch(spec=spec, result=result, cwd=cwd, timeout=config.runner_timeout)


def _with_role_skills(
    ctx: _Ctx, spec: runner.RunnerSpec, role: str | None, prompt: str, phase: str
) -> str:

    names = dispatch_brief.brief_skills(ctx.repo_root, spec.name, role, ctx.state.issue_type, phase)
    if not names:
        return prompt
    brief, missing = dispatch_brief.skill_brief(ctx.repo_root, names)
    return dispatch_brief.with_skills(prompt, brief, missing)


def sizing_at_dispatch(repo_root: Path, issue_id: str) -> dict[str, object]:

    with contextlib.suppress(RuntimeError, ValueError, OSError):
        sizing = decompose.dispatch_sizing(repo_root, issue_id)
        if sizing is not None:
            return sizing.record_inputs(repo_root)
    return {}


def _runner_block(
    ctx: _Ctx, dispatch: _Dispatch, *, issue_id: str, target: str
) -> AdvanceResult | None:

    spec, result = dispatch.spec, dispatch.result
    if result.timed_out:
        salvaged = _salvage_killed_run(issue_id, dispatch)
        return _blocked(
            ctx,
            f"runner {spec.name!r} stopped on "
            f"{runner.stop_label(result, dispatch.timeout)} in {target}; {salvaged.detail}",
        )
    if result.returncode != 0:
        tail = (result.stderr or result.stdout).strip().splitlines()
        detail = tail[-1] if tail else "no output"
        if len(detail) > 200:
            detail = detail[:200] + "…"
        return _blocked(
            ctx, f"runner {spec.name!r} failed in {target} (exit {result.returncode}): {detail}"
        )
    needs = needs_input.take(dispatch.cwd)
    if needs is not None:
        policy.record_needs_input(ctx.repo_root, issue_id, needs.fact)
        decisions.enqueue(ctx.repo_root, issue_id, "needs-input", needs.fact, needs.detail)
        reason = f"runner {spec.name!r} needs input in {target}: {needs.detail or needs.fact}"
        return _blocked(ctx, reason, needs_input=needs.fact)
    return None


def _salvage_killed_run(issue_id: str, dispatch: _Dispatch) -> commit.Salvage:

    salvaged = commit.salvage(
        dispatch.cwd, issue_id, reason=runner.stop_label(dispatch.result, dispatch.timeout)
    )
    advice = (
        "advance again to judge it"
        if salvaged.committed
        else "inspect the worktree and re-dispatch"
    )
    return replace(salvaged, detail=f"{salvaged.detail}; {advice}")


def record_run(
    repo_root: Path,
    issue_id: str,
    spec: runner.RunnerSpec,
    result: runner.RunResult,
    **inputs: object,
) -> None:

    runner.record_dispatch(repo_root, issue_id, spec, result, **inputs)  # type: ignore[arg-type]


@dataclass(frozen=True)
class _Proposal:
    work_type: str | None = None
    children: tuple[ChildSpec, ...] | None = None
    reason: str = ""
    by: str = ""


def _proposal_declined(block: str, proposal: _Proposal) -> str:
    return f"{block}; {proposal.reason}" if proposal.reason else block


def _proposal_payload(text: str) -> dict | None:

    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _run_proposer(ctx: _Ctx, kind: str, prompt: str, *, phase: str) -> tuple[str, str]:

    config = load_runner_config(ctx.repo_root)
    selected = runner.select_runner(config.specs, config.decider or config.default)
    if selected.kind == runner.HANDOFF:
        return "", (
            f"runner {selected.name!r} is a manual handoff, which proposes nothing; "
            "a headless runner is what originates an input"
        )
    spec = runner.confine_for_decider(selected)
    if spec is None:
        return "", (
            f"runner {selected.name!r} has no known tool-confinement overlay, so the {kind} "
            "proposer cannot be bounded to the issue's own requirement"
        )
    role = roles.resolve_role(ctx.repo_root, spec, phase)
    prompt = _with_role_skills(ctx, spec, role, prompt, phase)
    with runner.process_budget().slot(runner.DECIDER):
        result = runner.run(
            spec,
            prompt,
            ctx.repo_root,
            capture_usage=True,
            timeout=config.runner_timeout,
            role=role,
        )
    record_run(
        ctx.repo_root,
        ctx.issue_id,
        spec,
        result,
        prompt=prompt,
        phase=run_record.PROPOSE_PHASE,
    )
    if result.timed_out or result.handoff or result.returncode != 0:
        why = (
            f"the {kind} proposer hit runner_timeout ({config.runner_timeout:.0f}s)"
            if result.timed_out
            else f"the {kind} proposer's runner was unavailable or failed"
        )
        return "", why
    return runner.result_text(spec, result.stdout), ""


def _proposer_corpus(ctx: _Ctx, kind: str) -> tuple[str, str]:

    corpus = decisions.intake_corpus(ctx.repo_root, ctx.issue_id)
    if not corpus.strip():
        return "", (
            f"{ctx.issue_id} carries no description to propose a {kind} from, so there is "
            "no corpus to bound the proposer to"
        )
    return corpus, ""


def _proposal_grant(ctx: _Ctx, kind: str) -> policy.ProposalGrant:

    try:
        return policy.proposal_delegated(
            ctx.repo_root, ctx.issue_id, kind, ctx.grant_root or ctx.issue_id
        )
    except (RuntimeError, OSError, json.JSONDecodeError) as exc:
        return policy.ProposalGrant(False, f"the grant ledger could not be read: {exc}")


def _proposed_work_type(ctx: _Ctx) -> _Proposal:
    grant = _proposal_grant(ctx, "work_type")
    if not grant.allowed:
        return _Proposal(reason=grant.reason)
    corpus, missing = _proposer_corpus(ctx, "work type")
    if missing:
        return _Proposal(reason=missing)
    reply, refused = _run_proposer(
        ctx, "work-type", work_type_prompt(ctx.issue_id, corpus), phase="classify"
    )
    if refused:
        return _Proposal(reason=refused)
    payload = _proposal_payload(reply) or {}
    proposed = payload.get("work_type")
    proposed = proposed.strip() if isinstance(proposed, str) else None
    if proposed not in WORK_TYPES:
        return _Proposal(
            reason=f"the proposed work type {proposed!r} is not one of {list(WORK_TYPES)}"
        )
    return _Proposal(work_type=proposed, by=f"proposed under the {grant.level} grant")


def _proposed_children(  # noqa: PLR0911 — one return per distinct fall-back-to-human cause
    ctx: _Ctx,
) -> _Proposal:

    grant = _proposal_grant(ctx, "children")
    if not grant.allowed:
        return _Proposal(reason=grant.reason)
    corpus, missing = _proposer_corpus(ctx, "child plan")
    if missing:
        return _Proposal(reason=missing)
    sizing = load_sizing_config(ctx.repo_root)
    reply, refused = _run_proposer(
        ctx, "child-plan", child_plan_prompt(ctx.issue_id, corpus, sizing), phase="decompose"
    )
    if refused:
        return _Proposal(reason=refused)
    payload = _proposal_payload(reply)
    if payload is None:
        return _Proposal(reason="the child-plan proposal was not a single JSON object")
    try:
        children = decompose.parse_children(payload)
    except ValueError as exc:
        return _Proposal(reason=f"the proposed child plan failed the plan schema: {exc}")
    verdict = decompose.estimate_plan(ctx.repo_root, children, feature_id=ctx.issue_id)
    if verdict.refused:
        return _Proposal(
            reason="the sizing governor refused the proposed plan: " + "; ".join(verdict.violations)
        )
    return _Proposal(children=children, by=f"proposed under the {grant.level} grant")


def _build_evidence_block(ctx: _Ctx, worktree_name: str) -> AdvanceResult | None:

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

    live = {session.name for session in worktree.list_sessions(ctx.repo_root)}
    if not live:
        return {}
    known = merge.known_bead_ids(ctx.repo_root) or set()
    lanes = sorted(bead for bead in known if bead != ctx.issue_id and _worktree_name(bead) in live)
    return merge.declared_scopes(ctx.repo_root, lanes)


def _scope_block(ctx: _Ctx, worktree_name: str) -> AdvanceResult | None:

    declared = decompose.bead_class_and_scope(ctx.repo_root, ctx.issue_id)
    if declared is None or not declared[1]:
        return None
    session = worktree.load_session(worktree_name, ctx.repo_root)
    if session is None:
        return None
    changed = merge.branch_changed_paths(ctx.repo_root, session.base, session.branch)
    held = declared[1] + lane_scope(ctx.issue_id)
    outside = merge.out_of_scope_paths(changed, held)
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

    events = policy.record_unreliable_gate(
        ctx.repo_root, ctx.issue_id, merge.MERGE_GATE, result.detail
    )
    if events < policy.MAX_UNRELIABLE_GATE_EVENTS:
        return _blocked(ctx, result.detail, landing=result)
    answered = landing_gate.answered_unreliable_escalation(ctx.repo_root, ctx.issue_id)
    if answered is not None:
        return _blocked(
            ctx,
            f"{result.detail}; the escalation on gate {merge.MERGE_GATE} is already "
            f"answered by {answered.answered_by}: {answered.answer!r}, and that answer "
            "no longer authorises a landing — the flake is still there, so fix the gate "
            "and advance again",
            landing=result,
        )
    question = policy.unreliable_gate_escalation_question(merge.MERGE_GATE)
    decisions.enqueue(
        ctx.repo_root,
        ctx.issue_id,
        policy.REWORK_ESCALATION_KIND,
        question,
        result.detail,
    )
    return _blocked(ctx, f"escalated: {question}", landing=result)


def _shared_gate_landing_block(ctx: _Ctx, result: merge.MergeResult) -> AdvanceResult:

    policy.record_shared_gate_failure(
        ctx.repo_root, ctx.issue_id, merge.MERGE_GATE, result.culprits, result.detail
    )
    question = policy.shared_gate_escalation_question(merge.MERGE_GATE, result.culprits)
    answered = landing_gate.answered_shared_gate_escalation(ctx.repo_root, ctx.issue_id)
    if answered is not None:
        return _blocked(
            ctx,
            f"{result.detail}; the escalation on gate {merge.MERGE_GATE} is already "
            f"answered by {answered.answered_by}: {answered.answer!r}, and the record "
            "still fails the gate — fix it and advance again",
            landing=result,
        )
    decisions.enqueue(
        ctx.repo_root, ctx.issue_id, policy.REWORK_ESCALATION_KIND, question, result.detail
    )
    return _blocked(ctx, f"escalated: {question}", landing=result)


def _no_evidence_landing_block(ctx: _Ctx, result: merge.MergeResult) -> AdvanceResult | None:

    if result.unreliable:
        return _unreliable_landing_block(ctx, result)
    if result.foreign:
        return _shared_gate_landing_block(ctx, result)
    return None


def _verify_and_land(
    ctx: _Ctx, worktree_name: str, *, verify_mode: str | None = None
) -> AdvanceResult:

    for precondition in (_build_evidence_block, _scope_block):
        held = precondition(ctx, worktree_name)
        if held is not None:
            return held
    mode = verify_mode or ctx.inputs.verify_mode
    override = landing_gate.gate_override(ctx.repo_root, ctx.issue_id)
    changed = _changed_paths(ctx, worktree_name)
    result = merge.merge_worktree(
        ctx.repo_root,
        worktree_name,
        bead=ctx.issue_id,
        verify_mode=mode,
        override_gate=override is not None,
    )
    if override is not None and result.reached_gate:
        policy.spend_gate_override(ctx.repo_root, ctx.issue_id, override)
        result = replace(
            result, detail=f"{result.detail} (gate '{override}' skipped: answered 'land anyway')"
        )
    if result.status == merge.ALREADY_LANDED:
        return _record_verify(ctx, result.detail, verify_mode=mode)
    if result.status == "not-ready":
        return _blocked(ctx, result.detail, landing=result)
    if (held := _no_evidence_landing_block(ctx, result)) is not None:
        return held
    if not result.merged:
        return _rework(
            ctx,
            merge.MERGE_GATE,
            f"merge failed: {result.detail}",
            landing=result,
            findings=_landing_findings(result),
            evidence=_landing_evidence(result, mode),
        )
    held = _record_change_summary(ctx, changed, result)
    return held if held is not None else _record_verify(ctx, result.detail, verify_mode=mode)


def _changed_paths(ctx: _Ctx, worktree_name: str) -> tuple[str, ...] | None:

    if not handoff.adopted(ctx.repo_root, handoff.CHANGE_SUMMARY):
        return None
    session = worktree.load_session(worktree_name, ctx.repo_root)
    if session is None:
        return None
    return merge.branch_changed_paths(ctx.repo_root, session.base, session.branch)


def _record_change_summary(
    ctx: _Ctx, changed: tuple[str, ...] | None, result: merge.MergeResult
) -> AdvanceResult | None:

    if changed is None or not result.landed_head:
        return None
    payload = handoff.summary_payload(
        ctx.issue_id,
        ctx.state.title,
        (result.landed_head, changed),
        handoff.SelfCheck(result.status, result.detail, passed=result.merged),
    )
    try:
        handoff.record(ctx.repo_root, ctx.issue_id, handoff.CHANGE_SUMMARY, payload)
    except handoff.ArtifactError as exc:
        return _blocked(ctx, exc.verdict.reason, needs_input="artifact")
    return None


def _landing_findings(result: merge.MergeResult) -> tuple[str, ...]:

    if result.conflicted:
        return ()
    return (f"status={result.status}", result.detail)


def _landing_evidence(
    result: merge.MergeResult, mode: str
) -> tuple[repair_brief.GateEvidence, ...]:

    if result.status != repair_brief.LANDING_VERIFY_FAILED:
        return ()
    whole_suite = f"basicly verify --mode {mode}"
    if not result.checks:
        return (repair_brief.GateEvidence(check=f"verify {mode}", command=whole_suite),)
    return tuple(
        repair_brief.GateEvidence(
            check=check.name,
            command=" ".join(check.command) or whole_suite,
            output=repair_brief.clip_output(check.output or check.detail),
        )
        for check in result.checks
    )[: repair_brief.MAX_REPAIR_EVIDENCE]


def _repair_in_place(ctx: _Ctx, binding: loop_state.WorktreeBinding) -> AdvanceResult | None:

    if not ctx.repair_dispatch:
        return None
    session = _bound_session(ctx, binding)
    if session is None:
        return None
    cwd = Path(session.worktree_path)
    brief = repair_brief.take_repair_brief(cwd)
    where = f"worktree {binding.name!r}"
    before = merge.branch_head(ctx.repo_root, session.branch)
    stale = repair_brief.stale_against(brief, before) if brief is not None else ""
    if stale:
        _add_comment(ctx.repo_root, ctx.issue_id, f"{stale} ({where})")
    if brief is None or stale:
        return None
    return _repair_outcome(
        ctx,
        _run_agent(
            ctx, brief.issue_id, cwd, prompt=repair_brief.repair_prompt(brief), phase="repair"
        ),
        brief,
        where,
        branch=session.branch,
    )


def _repair_outcome(
    ctx: _Ctx,
    dispatch: _Dispatch,
    brief: repair_brief.RepairBrief,
    where: str,
    branch: str = "",
) -> AdvanceResult:

    if branch and merge.branch_head(ctx.repo_root, branch) == brief.branch_head:
        return _blocked(ctx, repair_brief.no_commit_reason(brief, where), needs_input="validation")
    if dispatch.result.handoff:
        return _blocked(
            ctx,
            f"repair for gate {brief.gate!r} handed to the driving agent in {where}; "
            "awaiting its work",
        )
    held = _runner_block(ctx, dispatch, issue_id=brief.issue_id, target=where)
    if held is not None:
        return held
    return _blocked(
        ctx,
        f"runner {dispatch.spec.name!r} repaired {brief.issue_id} in place against gate "
        f"{brief.gate!r} in {where}; advance again to re-run the gate",
    )


def _run_lane(ctx: _Ctx, binding: loop_state.WorktreeBinding) -> AdvanceResult:

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

    open_ids = [cid for cid, status in subtasks if loop_state.is_dispatchable(status)]
    if not open_ids:
        return _integrate_lane(ctx, binding, Path(session.worktree_path))
    blocked_ids = set(loop_state.blocked_ids(ctx.repo_root))
    runnable = [
        cid
        for cid in open_ids
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
            findings=report.failures,
            evidence=repair_brief.verify_evidence(report, cwd, _SUBTASK_VERIFY_MODE),
        )
    _write(
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

    return re.search(rf"{re.escape(bead_id)}(?![0-9A-Za-z._-])", message) is not None


def _subtask_committed(subtask_id: str, session: worktree.Session) -> bool:

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

    validate = _validate_lane(ctx, cwd)
    if validate is not None:
        return validate
    return _verify_and_land(ctx, binding.name, verify_mode=_LANE_VERIFY_MODE)


def _validate_lane(ctx: _Ctx, cwd: Path) -> AdvanceResult | None:

    selected = rubrics.select_rubrics(rubrics.load_rubrics(), ctx.state.issue_type)
    if not selected:
        return None
    verdicts = [
        verdict for rubric in selected for verdict in rubrics.evaluate(ctx.issue_id, rubric, cwd)
    ]
    rubrics.report_gate(ctx.repo_root, ctx.issue_id, verdicts)
    if rubrics.gate_status(verdicts) == "fail":
        failed = [
            verdict.check_id
            for verdict in verdicts
            if verdict.kind == rubrics.DETERMINISTIC and verdict.answer == rubrics.NO
        ]
        return _rework(
            ctx,
            rubrics.RUBRIC_GATE,
            f"lane validate failed: {', '.join(failed)}",
            findings=failed,
            evidence=_rubric_evidence(verdicts),
        )
    disputed = rubrics.judged_failures(verdicts)
    if disputed:
        return _hold_for_validate_decision(ctx, disputed)
    return None


def _rubric_evidence(
    verdicts: Sequence[rubrics.CheckVerdict],
) -> tuple[repair_brief.GateEvidence, ...]:

    return tuple(
        repair_brief.GateEvidence(
            check=verdict.check_id, output=repair_brief.clip_output(verdict.evidence)
        )
        for verdict in verdicts
        if verdict.kind == rubrics.DETERMINISTIC and verdict.answer == rubrics.NO
    )[: repair_brief.MAX_REPAIR_EVIDENCE]


def _hold_for_validate_decision(ctx: _Ctx, disputed: list[rubrics.CheckVerdict]) -> AdvanceResult:

    criteria = ", ".join(f"{v.check_id} ({v.severity})" for v in disputed)
    evidence = "; ".join(f"{verdict.check_id}: {verdict.evidence}" for verdict in disputed)
    decisions.enqueue(
        ctx.repo_root,
        ctx.issue_id,
        validate_gate.VALIDATE_DECISION_KIND,
        f"acceptance criteria unmet per the validator ({criteria}): accept, rework, or amend?",
        evidence,
    )
    return _blocked(
        ctx,
        f"lane validate disputed: {criteria} — queued as a decision, lane holds "
        "(dispose of it with `basicly loop answer`)",
        action="decision",
    )


def _build_children(ctx: _Ctx) -> AdvanceResult:

    children = _child_states(ctx)
    if not children:
        return _blocked(ctx, "decompose approved but no child tracks are recorded")
    _ensure_child_worktrees(ctx, children)
    still_open = [cid for cid, status in children if loop_state.is_dispatchable(status)]
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

    summary = handoff.entry_verdict(ctx.repo_root, ctx.issue_id, handoff.CHANGE_SUMMARY)
    if not summary.admitted:
        return _blocked(ctx, summary.reason, needs_input="artifact")
    report = verify.run_verify(ctx.repo_root, verify_mode or ctx.inputs.verify_mode)
    gate_error = _record_gate(ctx, ctx.issue_id, report)
    if gate_error is not None:
        return _blocked(ctx, gate_error)
    if not report.passed:
        return _rework(
            ctx,
            verify.DEFAULT_GATE,
            f"verify failed: {', '.join(report.failures)}",
            findings=report.failures,
        )
    return _moved(ctx, "verify", "merged", detail)


LANE_REWORK_CEILING_FACTOR = 2


def lane_rework_ceiling(config: PolicyConfig) -> int:
    return config.max_rework * LANE_REWORK_CEILING_FACTOR


def lane_rework_spent(repo_root: Path, issue_id: str, config: PolicyConfig) -> int | None:

    gates = dict.fromkeys((*config.required_gates, *repair_brief.REPAIR_GATES, merge.MERGE_GATE))
    try:
        return sum(policy.rework_charged(repo_root, issue_id, gate) for gate in gates)
    except RuntimeError, OSError, ValueError:
        return None


def _rework(  # noqa: PLR0913 — one parameter per recorded fact
    ctx: _Ctx,
    gate: str,
    reason: str,
    *,
    issue_id: str | None = None,
    landing: merge.MergeResult | None = None,
    findings: Sequence[str] = (),
    evidence: Sequence[repair_brief.GateEvidence] = (),
) -> AdvanceResult:

    target = issue_id or ctx.issue_id
    attempts = policy.record_rework(ctx.repo_root, target, gate)
    reason += _retrospective(ctx)
    convergence = (
        policy.record_finding_set(ctx.repo_root, target, gate, findings) if findings else None
    )
    if convergence is not None:
        stop = policy.finding_set_escalation(convergence)
        if stop is not None:
            return _escalate_stalled_rework(ctx, target, gate, f"{reason}; {stop}", landing)
        if convergence.stalled:
            reason = f"{reason}; warning: {convergence.detail}"
    capped = _lane_ceiling_block(ctx, target, gate, reason, landing)
    if capped is not None:
        return capped
    action = "escalated" if attempts >= ctx.config.max_rework else "blocked"
    suffix = ""
    if action == "escalated":
        decisions.enqueue(
            ctx.repo_root,
            target,
            policy.REWORK_ESCALATION_KIND,
            policy.rework_escalation_question(gate),
            reason,
        )
    else:
        suffix = _brief_repair(ctx, target, gate, reason, findings, evidence, landing)
    return _blocked(
        ctx,
        f"{reason} (rework {attempts}/{ctx.config.max_rework}){suffix}",
        action=action,
        landing=landing,
    )


def _retrospective(ctx: _Ctx) -> str:

    root = ctx.grant_root
    if root is None or not ctx.repair_dispatch:
        return ""
    signal = retrospective.evaluate(retrospective.read_ledger(ctx.repo_root, root))
    if not signal.fires:
        return ""
    if not retrospective.claim(ctx.repo_root, root, signal):
        return ""
    dispatch = _run_agent(
        ctx,
        root,
        ctx.repo_root,
        prompt=retrospective.prompt(root, signal),
        phase=retrospective.PHASE,
    )
    outcome = retrospective.settle(
        ctx.repo_root, root, runner.result_text(dispatch.spec, dispatch.result.stdout)
    )
    chart = signal.chart
    return (
        f"; retrospective fired ({signal.rule} on {signal.point}): {signal.detail} "
        f"[{chart.observations} units, centre {chart.centre:.2f}, sigma "
        f"{chart.sigma:.2f}, upper limit {chart.upper:.2f}]; outcome: {outcome}"
    )


def _lane_ceiling_block(
    ctx: _Ctx,
    target: str,
    gate: str,
    reason: str,
    landing: merge.MergeResult | None,
) -> AdvanceResult | None:

    ceiling = lane_rework_ceiling(ctx.config)
    spent = lane_rework_spent(ctx.repo_root, target, ctx.config)
    if spent is None or spent < ceiling:
        return None
    stop = (
        f"the lane has spent {spent} rework attempt(s) across its gates, at the total "
        f"ceiling of {ceiling} ({ctx.config.max_rework} per gate x "
        f"{LANE_REWORK_CEILING_FACTOR}); per-gate allowances stop compounding here — "
        "re-scope it, fix it by hand, or drop it"
    )
    decisions.enqueue(
        ctx.repo_root,
        target,
        policy.REWORK_ESCALATION_KIND,
        policy.rework_escalation_question(gate),
        f"{reason}; {stop}",
    )
    return _blocked(
        ctx,
        f"{reason}; {stop} (rework {spent}/{ceiling} total)",
        action="escalated",
        landing=landing,
    )


def _brief_repair(  # noqa: PLR0913 — one parameter per recorded fact
    ctx: _Ctx,
    target: str,
    gate: str,
    reason: str,
    findings: Sequence[str],
    evidence: Sequence[repair_brief.GateEvidence],
    landing: merge.MergeResult | None,
) -> str:

    repairable = gate in repair_brief.REPAIR_GATES or (
        landing is not None and landing.status == repair_brief.LANDING_VERIFY_FAILED
    )
    if not repairable or ctx.state.worktree is None:
        return ""
    session = _bound_session(ctx, ctx.state.worktree)
    if session is None:
        return ""
    brief = repair_brief.RepairBrief(
        issue_id=target,
        gate=gate,
        reason=reason,
        findings=tuple(findings),
        evidence=tuple(evidence)[: repair_brief.MAX_REPAIR_EVIDENCE],
        reviews=_recorded_reviews(ctx, target, gate),
        branch_head=merge.branch_head(ctx.repo_root, session.branch) or "",
    )
    if not repair_brief.write_repair_brief(Path(session.worktree_path), brief):
        return ""
    return f"; briefed a repair for gate {gate!r} in worktree {ctx.state.worktree.name!r}"


def _recorded_reviews(ctx: _Ctx, target: str, gate: str) -> tuple[lens_review.LensFindings, ...]:

    if gate != validate_gate.VALIDATE_GATE:
        return ()
    return tuple(
        lens_review.LensFindings(review.lens, repair_brief.clip_output(review.findings))
        for review in lens_review.latest_per_lens(ctx.repo_root, target)
    )


def _bound_session(ctx: _Ctx, binding: loop_state.WorktreeBinding) -> worktree.Session | None:

    try:
        return worktree.load_session(binding.name, ctx.repo_root)
    except RuntimeError, OSError, ValueError:
        return None


def _escalate_stalled_rework(
    ctx: _Ctx,
    target: str,
    gate: str,
    reason: str,
    landing: merge.MergeResult | None,
) -> AdvanceResult:

    refunded = policy.spend_convergence_refund(ctx.repo_root, target, gate)
    attempts = policy.rework_charged(ctx.repo_root, target, gate)
    spent = "this attempt refunded" if refunded else "the refund for this was already spent"
    decisions.enqueue(
        ctx.repo_root,
        target,
        policy.REWORK_ESCALATION_KIND,
        policy.rework_escalation_question(gate),
        reason,
    )
    return _blocked(
        ctx,
        f"{reason} (rework {attempts}/{ctx.config.max_rework}, {spent})",
        action="escalated",
        landing=landing,
    )


def _ensure_child_worktrees(ctx: _Ctx, children: list[tuple[str, str]]) -> None:

    wt_config = load_worktree_config(ctx.repo_root)
    sizing = load_sizing_config(ctx.repo_root)
    existing = {session.name for session in worktree.list_sessions(ctx.repo_root)}
    room = wt_config.concurrency - len(existing)
    open_children = {cid for cid, status in children if loop_state.is_dispatchable(status)}
    ranked = [
        node.issue_id
        for node in loop_state.ready_ranked(ctx.repo_root)
        if node.issue_id in open_children
    ]
    merge.commit_tracker_state(
        ctx.repo_root, ctx.issue_id, action="record the claim before provisioning"
    )
    for cid in ranked:
        if room <= 0:
            break
        name = _worktree_name(cid)
        if name in existing:
            continue
        if working_set.admit_working_set(ctx.repo_root, cid, sizing).refused:
            continue
        session = worktree.create(name, base=wt_config.base_branch, repo_root=ctx.repo_root)
        _bind_worktree(ctx, name, session.branch, issue_id=cid)
        existing.add(name)
        room -= 1


def ensure_lane_worktrees(
    repo_root: Path,
    root_issue: str,
    lanes: Sequence[tuple[str, str]],
    *,
    config: PolicyConfig | None = None,
) -> tuple[str, ...]:

    config = config or load_policy_config(repo_root)
    state = loop_state.read_node_state(repo_root, root_issue, config)
    ctx = _Ctx(repo_root, root_issue, state, config, Inputs())
    before = {session.name for session in worktree.list_sessions(repo_root)}
    _ensure_child_worktrees(ctx, list(lanes))
    after = {session.name for session in worktree.list_sessions(repo_root)}
    gained = after - before
    return tuple(issue_id for issue_id, _ in lanes if _worktree_name(issue_id) in gained)


def _bind_worktree(ctx: _Ctx, name: str, branch: str, *, issue_id: str | None = None) -> None:
    ref = loop_state.format_worktree_ref(name, branch)
    _write(ctx.repo_root, ["update", issue_id or ctx.issue_id, "--external-ref", ref])


def _child_states(ctx: _Ctx) -> list[tuple[str, str]]:
    record = tracker.require_record(ctx.repo_root, ctx.issue_id)
    dependents = record.get("dependents") or []
    return [
        (str(dep["id"]), str(dep.get("status", "")))
        for dep in dependents
        if isinstance(dep, dict) and dep.get("dependency_type") == "parent-child" and "id" in dep
    ]


def _worktree_name(issue_id: str) -> str:
    return issue_id.replace(".", "-")


_HANDLERS = {
    "intake": _on_intake,
    "classify": _on_classify,
    "decompose": _on_decompose,
    "build": _on_build,
    "verify": _on_verify,
    "validate": _on_validate,
    "ship": _on_ship,
}


def advance(  # noqa: PLR0913 — one keyword per independent driver choice
    repo_root: Path,
    issue_id: str,
    *,
    config: PolicyConfig | None = None,
    inputs: Inputs | None = None,
    grant_root: str | None = None,
    repair_dispatch: bool = True,
) -> AdvanceResult:

    config = config or load_policy_config(repo_root)
    inputs = inputs or Inputs()
    state = loop_state.read_node_state(repo_root, issue_id, config)
    if state.phase == "done":
        return AdvanceResult(issue_id, "done", "done", "done", "already shipped")

    ctx = _Ctx(repo_root, issue_id, state, config, inputs, grant_root, repair_dispatch)
    if state.phase in _BASE_CHECKOUT_PHASES and worktree.is_linked_checkout(repo_root):
        return _blocked(
            ctx,
            f"the {state.phase!r} transition merges/ships and must run from the base "
            f"checkout, not a linked worktree ({repo_root}); cd to the base checkout "
            "and re-run 'basicly loop advance'",
            needs_input="base-checkout",
        )
    if state.phase != "build":
        held = _evidence_block(ctx)
        if held is not None:
            return held
    return _HANDLERS[state.phase](ctx)


def run_until_blocked(  # noqa: PLR0913 — a thin driver carries advance's driver choices
    repo_root: Path,
    issue_id: str,
    *,
    config: PolicyConfig | None = None,
    inputs: Inputs | None = None,
    grant_root: str | None = None,
    max_steps: int = 20,
) -> list[AdvanceResult]:

    results: list[AdvanceResult] = []
    for _ in range(max_steps):
        result = advance(repo_root, issue_id, config=config, inputs=inputs, grant_root=grant_root)
        results.append(result)
        if result.blocked or result.to_phase == "done":
            break
    return results


@dataclass(frozen=True)
class CheckpointApproval:
    checkpoint: str
    detail: str = ""


CeremonyEvent = AdvanceResult | CheckpointApproval


@dataclass(frozen=True)
class CeremonyResult:
    events: tuple[CeremonyEvent, ...] = ()
    challenge: tuple[str, str] | None = None
    challenge_reason: str = ""
    refused: tuple[str, str] | None = None

    @property
    def steps(self) -> tuple[AdvanceResult, ...]:
        return tuple(event for event in self.events if isinstance(event, AdvanceResult))

    @property
    def blocked(self) -> bool:

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

    config = config or load_policy_config(repo_root)
    inputs = inputs or Inputs()
    codes = dict(confirms or {})
    events: list[CeremonyEvent] = []
    resolved: set[str] = set()
    for _ in range(max_steps):
        result = advance(repo_root, issue_id, config=config, inputs=inputs, grant_root=grant_root)
        events.append(result)
        if result.to_phase == "done":
            break
        if not result.blocked:
            continue
        name = result.checkpoint
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
