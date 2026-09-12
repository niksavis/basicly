# module-size-waiver: cost(basicly-0bj8q1): 4219 of 4000 tokens. Filling the lane card from


from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from . import (
    board_advance,
    board_fields,
    board_sections,
    board_serve,
    board_snapshot,
    board_unsupervised,
    checkout,
    config,
    decisions,
    loop_state,
    owned_store,
    policy,
    run_record,
    supervise,
    tracker,
    tracker_query,
    validate_gate,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence
    from typing import Any

UNREADABLE = (owned_store.TrackerDivergenceError, OSError, ValueError, KeyError, TypeError)

NO_SESSION = (*UNREADABLE, RuntimeError)


def session_facts(repo_root: Path) -> board_snapshot.SessionFacts | None:

    facts = board_serve.session_facts(repo_root)
    if facts is None:
        return None
    grant = active_grant(repo_root, facts.root_issue)
    return replace(
        facts,
        grant_level=grant.level if grant is not None else "",
        token_budget=grant.token_budget if grant is not None else None,
        spent_tokens=grant_spend(repo_root, facts.root_issue, grant),
    )


def active_grant(repo_root: Path, root_issue: str) -> policy.Grant | None:
    try:
        return policy.active_grant(repo_root, root_issue)
    except UNREADABLE:
        return None


@dataclass(frozen=True)
class SpendSources:
    population: dict[str, dict]
    local: dict[str, list] | None
    ledger: dict[str, list]
    union: dict[str, list]


@dataclass(frozen=True)
class SpendSplit:
    tokens: int
    local: int | None
    ledger: int
    dispatches_seen: int


def spend_sources(repo_root: Path) -> SpendSources:
    return SpendSources(
        population=policy.records_by_id(repo_root),
        local=run_record.load_run_records(repo_root),
        ledger=run_record.tracker_history(repo_root),
        union=run_record.dispatch_history(repo_root),
    )


def grant_split(
    repo_root: Path,
    root_issue: str,
    grant: policy.Grant | None,
    *,
    sources: SpendSources | None = None,
) -> SpendSplit | None:

    if grant is None:
        return None
    held = sources if sources is not None else spend_sources(repo_root)
    try:
        ids = policy.session_issue_ids(repo_root, root_issue, population=held.population)
        union = policy.session_spend(repo_root, root_issue, ids=ids, history=held.union)
        ledger = policy.session_spend(repo_root, root_issue, ids=ids, history=held.ledger)
        local = (
            None
            if held.local is None
            else policy.session_spend(repo_root, root_issue, ids=ids, history=held.local)
        )
    except UNREADABLE:
        return None
    if not union.dispatches_seen:
        return None
    under = policy.tokens_under_grant
    return SpendSplit(
        tokens=under(union.measured_tokens, grant),
        local=None if local is None else under(local.measured_tokens, grant),
        ledger=under(ledger.measured_tokens, grant),
        dispatches_seen=union.dispatches_seen,
    )


def grant_spend(
    repo_root: Path,
    root_issue: str,
    grant: policy.Grant | None,
    *,
    sources: SpendSources | None = None,
) -> int | None:

    split = grant_split(repo_root, root_issue, grant, sources=sources)
    return None if split is None else split.tokens


def live_grant_spend(session: board_snapshot.SessionFacts) -> int | None:

    if session.token_budget is None:
        return None
    live = sum(supervise.inflight_spend().values())
    if not live:
        return None
    return max(0, (session.spent_tokens or 0) + live)


def _with_live_spend(
    built: dict[str, object], session: board_snapshot.SessionFacts | None
) -> dict[str, object]:

    if session is None:
        return built
    section = built.get("session")
    if not isinstance(section, dict):
        return built
    live = live_grant_spend(session)
    if live is None:
        return built
    section["spent_tokens_live"] = live
    section["spent_tokens_live_over_estimate"] = True
    section["spent_tokens_live_bound"] = supervise.LIVE_OVERREPORT_BOUND
    return built


def repo_facts(repo_root: Path) -> board_sections.RepoFacts | None:

    try:
        state = checkout.git(["status", "--porcelain=v1", "-b"], cwd=repo_root, check=False)
        head = checkout.git(["rev-parse", "--short", "HEAD"], cwd=repo_root, check=False)
    except OSError:
        return None
    if state.returncode != 0:
        return None
    lines = state.stdout.splitlines()
    header = lines[0].removeprefix("## ") if lines else ""
    branch = "" if header.startswith("HEAD (no branch)") else header.partition("...")[0]
    return board_sections.RepoFacts(
        branch=branch,
        head=head.stdout.strip() if head.returncode == 0 else "",
        dirty=any(line.strip() for line in lines[1:]),
    )


def readiness(repo_root: Path) -> board_sections.Readiness | None:

    try:
        return board_sections.Readiness(
            ready=frozenset(
                str(row["record"]) for row in tracker_query.ready_report(repo_root)["records"]
            ),
            blocked=frozenset(
                str(row["record"]) for row in tracker_query.blocked_report(repo_root)["records"]
            ),
        )
    except UNREADABLE:
        return None


def phases(repo_root: Path) -> dict[str, str]:

    try:
        return loop_state.phase_map(repo_root)
    except UNREADABLE:
        return {}


_REWORK_FLAG = "rework"
_GATE_FIELD = "gate"


def _rework_counts(record: str, comments: Iterable[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for text in comments:
        row = board_fields.marker(record, "", text)
        if row is None or row.family != policy.MARKER or _REWORK_FLAG not in row.flags:
            continue
        if gate := row.fields.get(_GATE_FIELD, ""):
            counts[gate] = counts.get(gate, 0) + 1
    return counts


def _detail_fact(
    record: str, view: Any, defaults: config.PolicyConfig
) -> board_sections.DetailFacts:

    binding = loop_state.parse_worktree_ref(view.external_ref)
    held = tuple(
        name for name in config.CHECKPOINTS if policy.checkpoint_approved_in(view.comments, name)
    )
    counted = _rework_counts(record, view.comments)
    return board_sections.DetailFacts(
        id=record,
        worktree=binding.name if binding is not None else "",
        branch=binding.branch if binding is not None else "",
        checkpoints_held=held,
        checkpoints_missing=tuple(name for name in config.CHECKPOINTS if name not in held),
        rework={
            gate: counted.get(gate, 0)
            for gate in validate_gate.required_in(view.comments, defaults).required_gates
        },
        next_command=board_advance.remedy(record),
    )


def details(repo_root: Path) -> tuple[board_sections.DetailFacts, ...] | None:

    try:
        defaults = config.load_policy_config(repo_root)
        views = tracker.all_views(repo_root)
    except UNREADABLE:
        return None
    return tuple(_detail_fact(record, views[record], defaults) for record in sorted(views))


def questions(repo_root: Path, document: dict[str, object]) -> dict[str, str]:

    asks = document.get("asks")
    if not isinstance(asks, list):
        return {}
    found: dict[str, str] = {}
    for ask in asks:
        wait_id, issue = str(ask.get("wait_id", "")), str(ask.get("issue", ""))
        subject = str(ask.get("subject", ""))
        if not (wait_id and issue and subject):
            continue
        try:
            items = decisions.items_on(repo_root, issue)
        except UNREADABLE:
            continue
        for item in items:
            if item.pending and subject in item.question:
                found[wait_id] = item.question
    return found


def lane_facts(
    repo_root: Path,
    root_issue: str,
    phase_map: Mapping[str, str],
    *,
    lane_label: str | None = None,
) -> tuple[board_sections.LaneFacts, ...] | None:

    try:
        state = supervise.derive_session(repo_root, root_issue, lane_label=lane_label)
        views = [supervise.lane_view(repo_root, lane) for lane in state.adopted]
    except NO_SESSION:
        return None
    spending = supervise.inflight_spend()
    doing = supervise.inflight_activity()
    issued = supervise.inflight_dispatch()
    standings = supervise.lane_standings()
    runs = run_record.load_run_records(repo_root) or {}
    return tuple(
        _lane_fact(
            view,
            phase_map,
            spending,
            doing,
            runs.get(view.issue_id) or [],
            dispatch=issued,
            standing=standings.get(view.issue_id),
        )
        for view in views
    )


def _lane_fact(  # noqa: PLR0913 - one parameter per tier the card draws a figure from
    view: supervise.LaneView,
    phase_map: Mapping[str, str],
    spending: Mapping[str, int],
    doing: Mapping[str, str],
    runs: Sequence[Mapping[str, Any]],
    *,
    dispatch: Mapping[str, supervise.LaneStream] | None = None,
    standing: supervise.LaneStanding | None = None,
) -> board_sections.LaneFacts:

    running = view.issue_id in spending
    last = runs[-1] if runs else {}
    spent = spending.get(view.issue_id)
    meter = (dispatch or {}).get(view.issue_id) if running else None
    state = _standing_state(standing, running=running)
    return board_sections.LaneFacts(
        id=view.issue_id,
        phase=phase_map.get(view.issue_id, ""),
        status=view.status,
        state=state[0],
        state_detail=state[1],
        state_since=state[2],
        agent=(meter.agent if meter else "") or view.last_agent or _text(last.get("agent")),
        live=running,
        provisioned=view.live,
        started_at=meter.started_at if meter else (view.last_run_at or ""),
        tokens=(spent or None) if running else view.last_tokens,
        branch=view.branch,
        model=(meter.model if meter else "") or _text(last.get("model")),
        note=doing.get(view.issue_id, ""),
        cost_usd=None if running else _number(last.get("cost")),
        elapsed_s=meter.elapsed_s
        if meter
        else (None if running else _number(last.get("duration_s"))),
        context_used=None if running else _whole(last.get("context_tokens")),
        context_window=None if running else _whole(last.get("context_window")),
    )


def _standing_state(
    standing: supervise.LaneStanding | None, *, running: bool
) -> tuple[str, str, str]:

    if running:
        return supervise.LANE_RUNNING, "", ""
    if standing is None:
        return "", "", ""
    return standing.state, standing.detail, standing.since


def _text(held: object) -> str:
    return held if isinstance(held, str) else ""


def _number(held: object) -> float | None:
    return float(held) if isinstance(held, int | float) and not isinstance(held, bool) else None


def _whole(held: object) -> int | None:
    return held if isinstance(held, int) and not isinstance(held, bool) else None


def _visible_asks(
    asks: Sequence[Mapping[str, Any]], live_ids: frozenset[str], grant_level: str
) -> list[dict[str, object]]:

    covered = policy.GRANT_COVERAGE.get(grant_level, ())
    visible = []
    for ask in asks:
        if str(ask.get("issue", "")) not in live_ids:
            continue
        if ask.get("kind") == "checkpoint" and ask.get("subject") in covered:
            continue
        visible.append(dict(ask))
    return visible


def _hide_unanswerable(built: dict[str, object]) -> dict[str, object]:
    asks = built.get("asks")
    if not isinstance(asks, list):
        return built
    units = built.get("units")
    live_ids = (
        frozenset(str(row["id"]) for row in units if isinstance(row, dict) and "id" in row)
        if isinstance(units, list)
        else frozenset()
    )
    session = built.get("session")
    grant_level = str(session.get("grant_level", "")) if isinstance(session, dict) else ""
    built["asks"] = _visible_asks(asks, live_ids, grant_level)
    return built


def statuses(repo_root: Path) -> dict[str, str]:

    try:
        return {record: str(view.status) for record, view in tracker.all_views(repo_root).items()}
    except UNREADABLE:
        return {}


def document(
    repo_root: Path, *, lane_label: str | None = None, in_flight: bool = False
) -> dict[str, object]:

    phase_map = phases(repo_root)
    detail_rows = details(repo_root)
    moment = datetime.now(UTC)
    session = session_facts(repo_root)
    supervised = board_serve.live_holder(repo_root) is not None
    facts = board_snapshot.Facts(
        session=session,
        advances=lambda markers: board_advance.asks(
            loop_state.state_map(repo_root),
            lanes=None,
            supervised=supervised,
            last_event=board_advance.newest(markers),
        ),
        repo=repo_facts(repo_root),
        details=detail_rows,
        phases=phase_map,
        readiness=readiness(repo_root),
        lanes=(
            lane_facts(repo_root, session.root_issue, phase_map, lane_label=lane_label)
            if in_flight and session is not None
            else board_unsupervised.lanes(
                repo_root, detail_rows or (), phase_map, statuses(repo_root), moment
            )
            if board_serve.live_holder(repo_root) is None
            else None
        ),
    )
    built = board_snapshot.build_document(repo_root, facts=facts, now=moment)
    asked = questions(repo_root, built)
    if asked:
        built = board_snapshot.build_document(
            repo_root, facts=replace(facts, questions=asked), now=moment
        )
    return _with_live_spend(_hide_unanswerable(built), session)


def emit_tick(repo_root: Path, cadence_s: float, *, lane_label: str | None = None) -> Path:

    built = document(repo_root, lane_label=lane_label, in_flight=True)
    built["freshness"] = {
        "source": board_snapshot.SUPERVISOR_TICK,
        "cadence_s": cadence_s,
        "stale_after_s": supervise.STALE_AFTER_S,
    }
    return board_snapshot.write_document(repo_root, built)
