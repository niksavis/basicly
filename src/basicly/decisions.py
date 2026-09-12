from __future__ import annotations

import contextlib
import subprocess
import threading
from pathlib import Path

from . import policy, run_record, runner, tracker
from .config import (
    PolicyConfig,
    load_policy_config,
    load_runner_config,
)
from .decider_contract import (
    DECIDER_BY_PREFIX,
    DeciderVerdict,
    decider_prompt,
    intake_corpus,
    parse_verdict,
)
from .decision_marker import (
    BY_TOKEN,
    KINDS,
    DecisionItem,
    decision_id_for,
    items_by_id,
    render_answer,
    render_enqueue,
    split_decision_id,
)
from .tracker import add_comment as _add_comment

_QUEUE_LOCK = threading.Lock()


def enqueue(  # noqa: PLR0913 — mirrors the CLI surface
    repo_root: Path,
    issue_id: str,
    kind: str,
    question: str,
    detail: str = "",
    *,
    human_required: bool = True,
) -> DecisionItem:

    if kind not in KINDS:
        raise ValueError(f"unknown decision kind {kind!r}; expected one of {KINDS}")
    with _QUEUE_LOCK:
        items = items_by_id(repo_root, issue_id)
        generation = 1
        while True:
            decision_id = decision_id_for(issue_id, kind, question, generation)
            existing = items.get(decision_id)
            if existing is None:
                break
            if existing.pending:
                return existing
            generation += 1
        _add_comment(repo_root, issue_id, render_enqueue(decision_id, kind, question, detail))
        item = DecisionItem(
            decision_id=decision_id,
            issue_id=issue_id,
            kind=kind,
            question=question,
            detail=detail,
        )
    if human_required:
        _notify(repo_root, item)
    return item


def answer(  # noqa: PLR0913 — mirrors the CLI surface
    repo_root: Path,
    decision_id: str,
    text: str,
    *,
    by: str,
    rationale: str | None = None,
    confidence: float | None = None,
) -> DecisionItem:

    if not BY_TOKEN.match(by):
        raise ValueError(
            f"attribution {by!r} must match {BY_TOKEN.pattern} "
            "(single token; no spaces, '=', or newlines)"
        )
    issue_id, _ = split_decision_id(decision_id)
    item = items_by_id(repo_root, issue_id).get(decision_id)
    if item is None:
        raise ValueError(f"no decision {decision_id!r} recorded on {issue_id}")
    if not item.pending:
        raise ValueError(f"decision {decision_id!r} was already answered by {item.answered_by}")
    _add_comment(
        repo_root,
        issue_id,
        render_answer(decision_id, text, by=by, rationale=rationale, confidence=confidence),
    )
    _record_wait(repo_root, item, by)
    return DecisionItem(
        decision_id=decision_id,
        issue_id=issue_id,
        kind=item.kind,
        question=item.question,
        detail=item.detail,
        answer=text,
        answered_by=by,
        queued_at=item.queued_at,
    )


def _record_wait(repo_root: Path, item: DecisionItem, by: str) -> None:

    policy.record_wait(
        repo_root,
        item.issue_id,
        wait_id=item.decision_id,
        kind="decision",
        subject=item.kind,
        requested_at=item.queued_at,
        by=by,
        delegated=by.startswith(DECIDER_BY_PREFIX) or by == ENGINE_BY,
    )


def pending(repo_root: Path, root_issue: str) -> tuple[DecisionItem, ...]:

    closed = closed_ids(repo_root)
    items: list[DecisionItem] = []
    for issue_id in policy.session_issue_ids(repo_root, root_issue):
        if issue_id in closed:
            continue
        items += [i for i in items_by_id(repo_root, issue_id).values() if i.pending]
    return tuple(items)


def closed_ids(repo_root: Path) -> frozenset[str]:

    return frozenset(
        str(record["id"])
        for record in tracker.all_records(repo_root)
        if record.get("status") == "closed" and record.get("id")
    )


def settle_checkpoint(
    repo_root: Path, issue_id: str, name: str, *, by: str
) -> tuple[DecisionItem, ...]:

    settled: list[DecisionItem] = []
    for item in items_by_id(repo_root, issue_id).values():
        if item.kind != "checkpoint" or not item.pending or name not in item.question:
            continue
        settled.append(
            answer(
                repo_root,
                item.decision_id,
                f"the {name} checkpoint was approved, so the queued ask is settled",
                by=by,
            )
        )
    return tuple(settled)


def has_pending(repo_root: Path, issue_id: str) -> bool:

    return any(item.pending for item in items_by_id(repo_root, issue_id).values())


def items_on(repo_root: Path, issue_id: str) -> tuple[DecisionItem, ...]:

    return tuple(items_by_id(repo_root, issue_id).values())


def get(repo_root: Path, decision_id: str) -> DecisionItem | None:
    issue_id, _ = split_decision_id(decision_id)
    return items_by_id(repo_root, issue_id).get(decision_id)


def _notify(repo_root: Path, item: DecisionItem) -> None:

    argv = load_policy_config(repo_root).notify_command
    if not argv:
        return
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        subprocess.run(  # noqa: S603 — consumer-owned argv, list form, no shell
            [*argv, item.decision_id, item.question],
            check=False,
            capture_output=True,
            timeout=30,
        )


ENGINE_BY = "engine"


def decider_answers_count(repo_root: Path, root_issue: str) -> int:
    count = 0
    for issue_id in policy.session_issue_ids(repo_root, root_issue):
        for item in items_by_id(repo_root, issue_id).values():
            if item.answered_by and item.answered_by.startswith(DECIDER_BY_PREFIX):
                count += 1
    return count


def invoke_decider(  # noqa: PLR0911 — one return per distinct drop-to-human cause
    repo_root: Path,
    decision_id: str,
    root_issue: str,
    *,
    config: PolicyConfig | None = None,
) -> DecisionItem | DeciderVerdict:

    config = config or load_policy_config(repo_root)
    item = get(repo_root, decision_id)
    if item is None:
        raise ValueError(f"no decision {decision_id!r} recorded")
    if not item.pending:
        return item
    if decider_answers_count(repo_root, root_issue) >= config.decider_max_decisions:
        return DeciderVerdict(
            "",
            f"decider_max_decisions ({config.decider_max_decisions}) reached "
            "for this session; remaining decisions are human-only",
            0.0,
            abstain=True,
        )
    runner_config = load_runner_config(repo_root)
    selected = runner.select_runner(
        runner_config.specs, runner_config.decider or runner_config.default
    )
    spec = runner.confine_for_decider(selected)
    if spec is None:
        return DeciderVerdict(
            "",
            f"runner {selected.name!r} has no known tool-confinement overlay, so the decider "
            "cannot be bounded to the intake corpus; this decision stays human-only",
            0.0,
            abstain=True,
        )
    prompt = decider_prompt(item, intake_corpus(repo_root, root_issue))
    with runner.process_budget().slot(runner.DECIDER):
        result = runner.run(
            spec, prompt, repo_root, capture_usage=True, timeout=runner_config.runner_timeout
        )
    runner.record_dispatch(
        repo_root,
        item.issue_id,
        spec,
        result,
        prompt=prompt,
        phase=run_record.DECIDE_PHASE,
    )
    if result.timed_out or result.handoff or result.returncode != 0:
        why = (
            f"decider hit runner_timeout ({runner_config.runner_timeout:.0f}s)"
            if result.timed_out
            else "decider runner unavailable or failed"
        )
        return DeciderVerdict("", why, 0.0, abstain=True)
    verdict = parse_verdict(runner.result_text(spec, result.stdout))
    if verdict.abstain or not verdict.decision:
        return verdict
    with _QUEUE_LOCK:
        if decider_answers_count(repo_root, root_issue) >= config.decider_max_decisions:
            return DeciderVerdict(
                "",
                f"decider_max_decisions ({config.decider_max_decisions}) reached while "
                "this decision was being judged; it stays human-only",
                0.0,
                abstain=True,
            )
        return answer(
            repo_root,
            decision_id,
            verdict.decision,
            by=f"{DECIDER_BY_PREFIX}{spec.name}",
            rationale=verdict.rationale,
            confidence=verdict.confidence,
        )
