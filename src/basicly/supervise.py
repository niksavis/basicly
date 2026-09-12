from __future__ import annotations

import contextlib
import hashlib
import json
import os
import secrets
import subprocess
import threading
import time
from collections.abc import Callable, Iterable, Iterator, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from . import (
    commit,
    context_meter,
    decisions,
    decompose,
    health,
    label_source,
    lane_log,
    loop,
    loop_state,
    merge,
    needs_input,
    policy,
    provider_limit,
    repair_brief,
    roles,
    run_record,
    runner,
    tracker,
    wip,
    worktree,
)
from .config import (
    AUTONOMY_LEVELS,
    SizingConfig,
    load_runner_config,
    load_sizing_config,
    load_worktree_config,
)
from .redact import redact_secrets
from .working_set import (
    WorkingSetAdmission,
    admit_working_set,
    band_coverage,
    escalate_working_set,
)

LOCK_FILE = Path(".basicly/usage/supervisor.lock")

HEARTBEAT_INTERVAL_S = 15.0
STALE_AFTER_S = 60.0


class LockHeldError(RuntimeError):
    pass


class LockLostError(RuntimeError):
    pass


@dataclass(frozen=True)
class LockInfo:
    pid: int | None
    session_id: str | None
    root_issue: str | None
    age_s: float


def new_session_id(root_issue: str) -> str:
    return f"{root_issue}:{secrets.token_hex(4)}"


def _now() -> float:
    return time.time()


def read_holder(repo_root: Path) -> LockInfo | None:

    path = repo_root / LOCK_FILE
    try:
        age = _now() - path.stat().st_mtime
    except OSError:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    pid = data.get("pid")
    return LockInfo(
        pid=pid if isinstance(pid, int) else None,
        session_id=data.get("session_id") if isinstance(data.get("session_id"), str) else None,
        root_issue=data.get("root_issue") if isinstance(data.get("root_issue"), str) else None,
        age_s=age,
    )


def _create_lock(path: Path, payload: str) -> None:
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(payload)


def acquire(repo_root: Path, session_id: str, root_issue: str) -> Path:

    path = repo_root / LOCK_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    gitignore = path.parent / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("*\n", encoding="utf-8")

    payload = json.dumps(
        {"pid": os.getpid(), "session_id": session_id, "root_issue": root_issue},
        indent=2,
        sort_keys=True,
    )
    try:
        _create_lock(path, payload)
    except FileExistsError:
        pass
    else:
        return path

    holder = read_holder(repo_root)
    if holder is None:
        try:
            _create_lock(path, payload)
        except FileExistsError as exc:
            raise LockHeldError("another supervisor acquired the freed lock first") from exc
        else:
            return path
    if holder.age_s < STALE_AFTER_S:
        raise LockHeldError(
            f"supervisor {holder.session_id or 'unknown'} (pid {holder.pid or '?'}) holds the "
            f"lock, heartbeat {holder.age_s:.0f}s old (stale after {STALE_AFTER_S:.0f}s)"
        )
    tombstone = path.with_name(f"{path.name}.stale.{os.getpid()}")
    try:
        path.replace(tombstone)
    except OSError as exc:
        raise LockHeldError("another supervisor is taking over the stale lock") from exc
    tombstone.unlink(missing_ok=True)
    try:
        _create_lock(path, payload)
    except FileExistsError as exc:
        raise LockHeldError("another supervisor re-created the lock during takeover") from exc
    return path


def heartbeat(lock_path: Path, session_id: str) -> None:

    try:
        data = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LockLostError("supervisor lock vanished; a contender took over") from exc
    if not (isinstance(data, dict) and data.get("session_id") == session_id):
        raise LockLostError("supervisor lock now belongs to a successor session")
    os.utime(lock_path, None)


def release(lock_path: Path, session_id: str) -> None:

    try:
        data = json.loads(lock_path.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError:
        return
    if isinstance(data, dict) and data.get("session_id") == session_id:
        lock_path.unlink(missing_ok=True)


class HeartbeatThread(threading.Thread):
    def __init__(
        self,
        lock_path: Path,
        session_id: str,
        interval: float = HEARTBEAT_INTERVAL_S,
        *,
        board: Callable[[float], object] | None = None,
        report: Callable[[str], None] | None = None,
    ) -> None:

        super().__init__(name="supervisor-heartbeat", daemon=True)
        self._lock_path = lock_path
        self._session_id = session_id
        self._interval = interval
        self._board = board
        self._report = report
        self._stopped = threading.Event()
        self.lost: LockLostError | None = None

    def run(self) -> None:
        while not self._stopped.wait(self._interval):
            try:
                heartbeat(self._lock_path, self._session_id)
            except LockLostError as exc:
                self.lost = exc
                return
            self._emit_board()

    def _emit_board(self) -> None:

        if self._board is None:
            return
        try:
            self._board(self._interval)
        except Exception as exc:  # noqa: BLE001 — a board must never fail a pass
            _say(self._report, f"board:    snapshot not written - {exc}")

    def stop(self) -> None:
        self._stopped.set()

    def check(self) -> None:
        if self.lost is not None:
            raise self.lost


STOP_FILE = Path(".basicly/usage/supervisor.stop")


@dataclass(frozen=True)
class StopRequest:
    root_issue: str
    requested_by: str
    reason: str


def request_stop(repo_root: Path, root_issue: str, *, requested_by: str, reason: str) -> Path:

    path = repo_root / STOP_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"root_issue": root_issue, "requested_by": requested_by, "reason": reason},
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return path


def take_stop_request(repo_root: Path, root_issue: str) -> StopRequest | None:

    path = repo_root / STOP_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or data.get("root_issue") != root_issue:
        return None
    path.unlink(missing_ok=True)
    return StopRequest(
        root_issue=root_issue,
        requested_by=str(data.get("requested_by") or "unknown"),
        reason=str(data.get("reason") or ""),
    )


def holds_lock(repo_root: Path, session_id: str) -> bool:

    holder = read_holder(repo_root)
    return holder is not None and holder.session_id == session_id


def await_session_return(repo_root: Path, session_id: str, *, poll_s: float = 2.0) -> None:

    while holds_lock(repo_root, session_id):
        time.sleep(poll_s)


def session_end_reason(
    repo_root: Path,
    state: SessionState,
    *,
    passes: int,
    limit: int | None,
    carried: frozenset[str] = frozenset(),
) -> str | None:

    stop = take_stop_request(repo_root, state.root_issue)
    if stop is None and not (limit is not None and passes >= limit):
        return None
    ended = (
        f"stopped:  requested by {stop.requested_by} - {stop.reason}"
        if stop is not None
        else f"stopped:  --max-passes {limit} reached after {passes} pass(es), "
        f"{len(state.open_children)} child(ren) still open"
    )
    if carried:
        ended += f"; green and committed, not landed: {', '.join(sorted(carried))}"
    return ended


@dataclass(frozen=True)
class AdoptedLane:
    issue_id: str
    status: str
    binding: loop_state.WorktreeBinding
    live: bool


@dataclass(frozen=True)
class SessionState:
    root_issue: str
    root_status: str
    children: tuple[tuple[str, str], ...]
    adopted: tuple[AdoptedLane, ...]
    lane_label: str | None = None
    session_id: str | None = None

    @property
    def log_session(self) -> str:

        return self.session_id or self.root_issue

    @property
    def open_children(self) -> tuple[str, ...]:

        return tuple(cid for cid, status in self.children if loop_state.is_dispatchable(status))

    @property
    def done(self) -> bool:

        if self.root_status == "closed":
            return True
        return bool(self.children) and not self.open_children


class LaneSelectionError(RuntimeError):
    pass


def lane_selection(
    repo_root: Path, label: str, *, exclude: Iterable[str] = ()
) -> tuple[tuple[str, str], ...]:

    selected = label_source.labelled(repo_root, label)
    for issue_id in exclude:
        selected.pop(issue_id, None)
    if not selected:
        raise LaneSelectionError(
            f"no bead outside the pass root carries label {label!r}; "
            f"label the lanes first "
            f"(basicly tracker write -- update <id> --add-label {label})"
        )
    return tuple(sorted(selected.items()))


def derive_session(
    repo_root: Path,
    root_issue: str,
    *,
    lane_label: str | None = None,
    session_id: str | None = None,
) -> SessionState:

    record = tracker.require_record(repo_root, root_issue)

    if lane_label is not None:
        children = lane_selection(repo_root, lane_label, exclude=(root_issue,))
    else:
        children = tuple(
            (str(dep["id"]), str(dep.get("status", "")))
            for dep in record.get("dependents") or []
            if isinstance(dep, dict)
            and dep.get("dependency_type") == "parent-child"
            and "id" in dep
        )

    live_names = {session.name for session in worktree.list_sessions(repo_root)}
    adopted: list[AdoptedLane] = []
    candidates = [(root_issue, str(record.get("status", "")))]
    candidates += [(cid, status) for cid, status in children]
    for issue_id, status in candidates:
        if status == "closed":
            continue
        binding = _binding_of(repo_root, issue_id, record if issue_id == root_issue else None)
        if binding is None:
            continue
        adopted.append(
            AdoptedLane(
                issue_id=issue_id,
                status=status,
                binding=binding,
                live=binding.name in live_names,
            )
        )

    return SessionState(
        root_issue=root_issue,
        root_status=str(record.get("status", "")),
        children=children,
        adopted=tuple(adopted),
        lane_label=lane_label,
        session_id=session_id,
    )


def _show_issue(repo_root: Path, issue_id: str) -> dict | None:
    return tracker.read_record(repo_root, issue_id)


def _binding_of(
    repo_root: Path, issue_id: str, record: dict | None
) -> loop_state.WorktreeBinding | None:
    if record is None:
        record = _show_issue(repo_root, issue_id)
        if record is None:
            return None
    return loop_state.parse_worktree_ref(record.get("external_ref"))


@dataclass(frozen=True)
class LaneView:
    issue_id: str
    status: str
    worktree: str
    branch: str
    live: bool
    last_agent: str | None = None
    last_outcome: str | None = None
    last_run_at: str | None = None
    last_tokens: int | None = None


@dataclass(frozen=True)
class Observation:
    root_issue: str
    root_status: str
    children_total: int
    children_open: int
    done: bool
    lanes: tuple[LaneView, ...]
    pending_decisions: tuple[decisions.DecisionItem, ...]
    lane_label: str | None = None
    holder: LockInfo | None = None
    holder_stale: bool = False
    holder_on_this_root: bool = False
    grant_level: str | None = None
    token_budget: int | None = None
    spent_tokens: int = 0
    human_wait_s: int = 0
    delegated_wait_s: int = 0
    dispatch_s: float = 0.0

    @property
    def supervised(self) -> bool:
        return self.holder is not None and self.holder_on_this_root and not self.holder_stale


def observe(repo_root: Path, root_issue: str, *, lane_label: str | None = None) -> Observation:

    state = derive_session(repo_root, root_issue, lane_label=lane_label)
    holder = read_holder(repo_root)
    grant = policy.active_grant(repo_root, root_issue)
    wait = policy.session_wait_summary(repo_root, root_issue)
    return Observation(
        root_issue=state.root_issue,
        root_status=state.root_status,
        children_total=len(state.children),
        children_open=len(state.open_children),
        done=state.done,
        lanes=tuple(lane_view(repo_root, lane) for lane in state.adopted),
        pending_decisions=decisions.pending(repo_root, root_issue),
        lane_label=lane_label,
        holder=holder,
        holder_stale=holder is not None and holder.age_s >= STALE_AFTER_S,
        holder_on_this_root=holder is not None and holder.root_issue == root_issue,
        grant_level=grant.level if grant is not None else None,
        token_budget=grant.token_budget if grant is not None else None,
        spent_tokens=policy.session_spend(repo_root, root_issue).measured_tokens,
        human_wait_s=wait.human_wait_s,
        delegated_wait_s=wait.delegated_wait_s,
        dispatch_s=wait.dispatch_s,
    )


def lane_view(repo_root: Path, lane: AdoptedLane) -> LaneView:

    latest = run_record.latest_record(repo_root, lane.issue_id)
    return LaneView(
        issue_id=lane.issue_id,
        status=lane.status,
        worktree=lane.binding.name,
        branch=lane.binding.branch,
        live=lane.live,
        last_agent=latest.agent if latest is not None else None,
        last_outcome=latest.outcome if latest is not None else None,
        last_run_at=latest.timestamp if latest is not None else None,
        last_tokens=latest.tokens if latest is not None else None,
    )


INFO_MARKER = "[harness-info]"

FOUND_INFO_KINDS = ("coupling", "constraint", "decision", "fact")

_MAX_INFO_SUMMARY = 200
_MAX_INFO_DETAIL = 500
_MAX_FOLDED_RECORDS = 20


@dataclass(frozen=True)
class FoundInfo:
    kind: str
    summary: str
    detail: str = ""
    affects: tuple[str, ...] = ()
    source: str = ""


def _folded_ref(info: FoundInfo) -> str:

    digest = hashlib.sha256(info.summary.encode("utf-8")).hexdigest()[:8]
    return f"{info.source or '?'}#{info.kind}-{digest}"


def record_found_info(repo_root: Path, issue_id: str, info: FoundInfo) -> None:

    if info.kind not in FOUND_INFO_KINDS:
        raise ValueError(
            f"unknown found-info kind {info.kind!r}; expected one of {FOUND_INFO_KINDS}"
        )
    payload = json.dumps(
        {
            "kind": info.kind,
            "summary": info.summary,
            "detail": info.detail,
            "affects": list(info.affects),
        },
        sort_keys=True,
    )
    tracker.add_comment(repo_root, issue_id, f"{INFO_MARKER} {payload}")


def parse_found_info(text: str, source: str) -> FoundInfo | None:

    stripped = text.strip()
    if not stripped.startswith(INFO_MARKER):
        return None
    try:
        data = json.loads(stripped[len(INFO_MARKER) :].strip())
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    kind = data.get("kind")
    summary = data.get("summary")
    if kind not in FOUND_INFO_KINDS or not isinstance(summary, str) or not summary.strip():
        return None
    detail = data.get("detail")
    raw_affects = data.get("affects")
    affects = (
        tuple(a.strip() for a in raw_affects if isinstance(a, str) and a.strip())
        if isinstance(raw_affects, list)
        else ()
    )
    return FoundInfo(
        kind=kind,
        summary=summary.strip()[:_MAX_INFO_SUMMARY],
        detail=detail.strip()[:_MAX_INFO_DETAIL] if isinstance(detail, str) else "",
        affects=affects,
        source=source,
    )


def found_info_records(repo_root: Path, issue_ids: Iterable[str]) -> tuple[FoundInfo, ...]:
    records: list[FoundInfo] = []
    for issue_id in issue_ids:
        for row in tracker.read_comments(repo_root, issue_id):
            info = parse_found_info(str(row.get(tracker.COMMENT_TEXT_KEY, "")), source=issue_id)
            if info is not None:
                records.append(info)
    return tuple(records)


@dataclass(frozen=True)
class DispatchBundle:
    issue_id: str
    prompt: str
    folded: tuple[FoundInfo, ...]
    answers: tuple[decisions.DecisionItem, ...] = ()


def build_bundle(
    repo_root: Path,
    issue_id: str,
    *,
    known_ids: frozenset[str] = frozenset(),
    cwd: Path | None = None,
) -> DispatchBundle:

    record = _show_issue(repo_root, issue_id) or {}
    scope = decompose.parse_scope_section(str(record.get("description") or ""))
    sources = sorted({issue_id, *known_ids})
    records = found_info_records(repo_root, sources)
    matching = [r for r in records if _info_matches(r, issue_id, scope, known_ids)]
    folded = tuple(matching[-_MAX_FOLDED_RECORDS:])
    repair = repair_brief.take_repair_brief(cwd) if cwd is not None else None
    prompt = (
        repair_brief.repair_prompt(repair) if repair is not None else loop.dispatch_prompt(issue_id)
    )
    if folded:
        lines = []
        for info in folded:
            line = f"- [{info.kind}] {info.summary}"
            if info.detail:
                line += f" — {info.detail}"
            lines.append(line + f" (recorded on {info.source})")
        prompt += (
            "\n\nCross-lane findings recorded since this work was planned; "
            "fold them into your approach:\n" + "\n".join(lines)
        )
    answers = answered_decisions(repo_root, issue_id)
    if answers:
        prompt += (
            "\n\nQuestions this work already blocked on, and the answers on "
            "record — treat them as decided, do not re-ask:\n"
            + "\n".join(
                f"- {item.question} → {item.answer} (answered by {item.answered_by})"
                for item in answers
            )
        )
    return DispatchBundle(issue_id=issue_id, prompt=prompt, folded=folded, answers=answers)


def answered_decisions(repo_root: Path, issue_id: str) -> tuple[decisions.DecisionItem, ...]:

    items = [item for item in decisions.items_on(repo_root, issue_id) if not item.pending]
    return tuple(items[-_MAX_FOLDED_RECORDS:])


def _info_matches(
    info: FoundInfo, issue_id: str, scope: tuple[str, ...], known_ids: frozenset[str]
) -> bool:

    for entry in info.affects:
        if entry == issue_id:
            return True
        if entry == info.source or entry in known_ids:
            continue
        if scope and decompose.scopes_overlap((entry,), scope):
            return True
    return False


def coupled_beads(
    repo_root: Path, info: FoundInfo, candidates: Iterable[str], known_ids: frozenset[str]
) -> tuple[str, ...]:

    found: list[str] = []
    for bead in sorted(candidates):
        if bead == info.source:
            continue
        read = decompose.bead_class_and_scope(repo_root, bead)
        scope = read[1] if read is not None else ()
        if _info_matches(info, bead, scope, known_ids):
            found.append(bead)
    return tuple(found)


def propose_coupling_edges(
    repo_root: Path, session: SessionState
) -> tuple[tuple[str, str, str], ...]:

    open_children = frozenset(session.open_children)
    known = frozenset({session.root_issue, *(cid for cid, _ in session.children)})
    in_flight = {lane.issue_id for lane in session.adopted if lane.live}
    recorded: list[tuple[str, str, str]] = []
    for info in found_info_records(repo_root, sorted(known)):
        if info.kind != "coupling":
            continue
        for bead in coupled_beads(repo_root, info, open_children, known):
            if _already_coupled(repo_root, bead, info.source):
                continue
            if bead in in_flight:
                merge.record_coupling(repo_root, bead, info.source)
                recorded.append((bead, info.source, merge.COUPLING_DEP_TYPE))
            else:
                tracker.try_write(repo_root, ["dep", "add", bead, info.source, "-t", "blocks"])
                recorded.append((bead, info.source, "blocks"))
    return tuple(recorded)


def _already_coupled(repo_root: Path, bead: str, coupled_to: str) -> bool:

    try:
        record = _show_issue(repo_root, bead)
    except RuntimeError, OSError, ValueError:
        return True
    if record is None:
        return True
    for dep in record.get("dependencies") or []:
        if not isinstance(dep, dict):
            continue
        if (dep.get("depends_on_id") or dep.get("id")) == coupled_to:
            return True
    return False


@dataclass(frozen=True)
class PassSpendAdmission:
    forecast_tokens: int | None
    remaining_tokens: int | None
    counted: tuple[str, ...]
    unforecast: tuple[str, ...]
    warning: str | None
    assumed: tuple[str, ...] = ()
    assumed_source: str = ""

    @property
    def coverage(self) -> str:

        if self.forecast_tokens is None:
            return "no lanes to start"
        parts = [f"{self.forecast_tokens} tokens forecast"]
        if self.remaining_tokens is not None:
            parts.append(f"{self.remaining_tokens} remaining under the grant")
        else:
            parts.append("no ceiling applies")
        if self.counted:
            parts.append(f"sized: {', '.join(self.counted)}")
        if self.assumed:
            parts.append(
                f"assumed at the unsizeable-lane bound ({self.assumed_source}): "
                f"{', '.join(self.assumed)}"
            )
        if self.unforecast:
            parts.append(f"UNBOUNDED, no figure at all: {', '.join(self.unforecast)}")
        return "; ".join(parts)


def admit_pass_spend(
    repo_root: Path,
    working_sets: tuple[WorkingSetAdmission, ...],
    status: policy.SpendStatus,
    sizing: SizingConfig,
) -> PassSpendAdmission:

    dispatching = tuple(item for item in working_sets if not item.refused)
    if not dispatching:
        return PassSpendAdmission(None, status.remaining_tokens, (), (), None)
    sized = tuple(item for item in dispatching if item.sizing is not None)
    forecasts: tuple[decompose.SpendForecast, ...] = ()
    if sized:
        with contextlib.suppress(RuntimeError, ValueError, OSError):
            forecasts = decompose.dispatch_spend_forecasts(
                repo_root, tuple(item.sizing for item in sized if item.sizing is not None), sizing
            )
    counted: list[str] = []
    total = 0
    if forecasts:
        for item, forecast in zip(sized, forecasts, strict=True):
            if forecast.tokens is None:
                continue
            counted.append(item.issue_id)
            total += forecast.tokens
    assumed = tuple(
        item.issue_id for item in dispatching if item.issue_id not in frozenset(counted)
    )
    assumed_source = ""
    if assumed:
        per_lane, assumed_source = decompose.unsized_lane_tokens(repo_root, sizing)
        total += per_lane * len(assumed)
    return PassSpendAdmission(
        forecast_tokens=total,
        remaining_tokens=status.remaining_tokens,
        counted=tuple(counted),
        unforecast=(),
        warning=policy.check_pass_spend(total, status),
        assumed=assumed,
        assumed_source=assumed_source,
    )


def _stopped_clause(agent: dict[str, Any]) -> str:

    bounds: dict[str, int] = agent["stopped_bounds"]
    if not bounds:
        return "0 stopped by a bound"
    named = ", ".join(f"{bound} {count}" for bound, count in bounds.items())
    return f"{agent['stopped']} stopped by a bound ({named})"


def health_coverage(repo_root: Path) -> tuple[str, str]:

    report = health.health_report(repo_root)
    agents = report["agents"]
    if not agents:
        return "no run-records yet", "no history to drift against"
    scored = "; ".join(
        f"{agent['agent']} {agent['health_score']:.2f} over {agent['runs']} runs "
        f"(fail {agent['failure_rate']:.0%}, {_stopped_clause(agent)}, "
        f"rework {agent['rework_rate']:.0%} — "
        f"{agent['rework_beads']} bead(s) re-dispatched)"
        for agent in agents
    )
    drift = report["drift"]
    regressed = [entry for entry in drift if entry["regressed"]]
    if not regressed:
        return scored, f"no behavioral regression across {len(drift)} agent(s)"
    return scored, "; ".join(
        f"REGRESSED {entry['agent']}: fail {entry['recent_failure_rate']:.0%} over the "
        f"recent {entry['recent_runs']} vs {entry['baseline_failure_rate']:.0%} over "
        f"{entry['baseline_runs']} baseline runs ({entry['delta']:+.2f})"
        for entry in regressed
    )


def _report_coverage(
    report: Callable[[str], None] | None,
    repo_root: Path,
    working_sets: tuple[WorkingSetAdmission, ...],
    pass_spend: PassSpendAdmission,
) -> None:

    if report is None:
        return
    report(f"band:     {band_coverage(working_sets)}")
    report(f"spend:    {pass_spend.coverage}")
    scored, drifted = health_coverage(repo_root)
    report(f"health:   {scored}")
    report(f"drift:    {drifted}")


PARKED_LANE_QUESTION = (
    "this lane is parked downstream of build and the pass cannot advance it: "
    "resolve what it waits on, or park it?"
)


def metered_without_a_budget(repo_root: Path, admission: policy.SpendStatus) -> str | None:

    if admission.grant is not None and admission.grant.token_budget is not None:
        return None
    config = load_runner_config(repo_root)
    spec = runner.select_runner(config.specs, config.default, capable=runner.is_capable)
    return spec.name if spec.kind == runner.HEADLESS else None


@dataclass(frozen=True)
class LaneOutcome:
    issue_id: str
    runner_name: str
    result: runner.RunResult | None
    needs_fact: str | None
    occupancy: int | None
    overrun: bool
    detail: str
    dispatched: bool = True
    refused: bool = False
    salvaged: bool = False
    transient: bool = False
    model: str | None = None
    model_tier: str | None = None
    model_source: str | None = None
    observed_models: tuple[str, ...] = ()
    tier_honoured: bool | None = None
    provider_refusal: str = ""
    spend: runner.Usage | None = None

    @property
    def spend_note(self) -> str:

        if self.spend is None:
            return ""
        return f", {self.spend.tokens} tokens{' (estimated)' if self.spend.estimated else ''}"

    @property
    def model_note(self) -> str:
        parts: list[str] = []
        if self.model_tier:
            asked = f"tier {self.model_tier}"
            if self.model_source:
                asked += f" via {self.model_source}"
            parts.append(asked)
        if self.model:
            parts.append(f"model {self.model}")
        observed = tuple(m for m in self.observed_models if m)
        if observed and (self.model is None or set(observed) != {self.model}):
            parts.append(f"observed {', '.join(observed)}")
        if self.tier_honoured is False:
            parts.append("TIER NOT HONOURED")
        return "; ".join(parts)


class Unstarted(Enum):
    REFUSED = "refused"
    STOPPED = "stopped"
    TRANSIENT = "transient"
    CARRIED = "carried"


class ProviderGate:
    def __init__(self) -> None:
        self._shut = threading.Event()

    def latch(self, outcome: LaneOutcome) -> LaneOutcome:
        if outcome.provider_refusal:
            self._shut.set()
        return outcome

    def declined(self, issue_id: str, runner_name: str) -> LaneOutcome | None:

        if not self._shut.is_set():
            return None
        return _unstarted(
            issue_id,
            runner_name,
            f"not started: {provider_limit.LIMIT_QUESTION}",
            Unstarted.REFUSED,
        )


def _unstarted(issue_id: str, runner_name: str, detail: str, why: Unstarted) -> LaneOutcome:

    return LaneOutcome(
        issue_id=issue_id,
        runner_name=runner_name,
        result=None,
        needs_fact=None,
        occupancy=None,
        overrun=False,
        detail=detail,
        dispatched=why is not Unstarted.CARRIED,
        refused=why is Unstarted.REFUSED,
        transient=why is Unstarted.TRANSIENT,
    )


def _declined_start(issue_id: str, runner_name: str, gate: ProviderGate) -> LaneOutcome | None:

    declined = gate.declined(issue_id, runner_name)
    if declined is not None:
        note_standing(LANE_REFUSED, declined.detail, issue_id)
    return declined


def ready_lanes(
    repo_root: Path, session: SessionState, *, skip: frozenset[str] = frozenset()
) -> tuple[AdoptedLane, ...]:

    blocked = set(loop_state.blocked_ids(repo_root))
    ranks = {node.issue_id: node.rank for node in loop_state.ready_ranked(repo_root)}
    live = [
        lane
        for lane in session.adopted
        if lane.live
        and loop_state.is_dispatchable(lane.status)
        and lane.issue_id not in blocked
        and lane.issue_id not in skip
        and not decisions.has_pending(repo_root, lane.issue_id)
        and _phase_of(repo_root, lane.issue_id) == "build"
        and not _has_subtasks(repo_root, lane.issue_id)
    ]
    return tuple(
        sorted(live, key=lambda lane: (ranks.get(lane.issue_id, float("inf")), lane.issue_id))
    )


def _phase_of(repo_root: Path, issue_id: str) -> str:
    return loop_state.read_node_state(repo_root, issue_id).phase


def _has_subtasks(repo_root: Path, issue_id: str) -> bool:

    record = _show_issue(repo_root, issue_id) or {}
    return any(
        isinstance(dep, dict) and dep.get("dependency_type") == "parent-child"
        for dep in record.get("dependents") or []
    )


def configure_budget(repo_root: Path) -> runner.ProcessBudget:

    return runner.configure_process_budget(
        load_runner_config(repo_root).max_agent_processes,
        load_worktree_config(repo_root).concurrency,
    )


DELEGABLE_KINDS = ("escalation", "needs-input")

_MIN_DELEGATION_LEVEL = "L2"


@dataclass(frozen=True)
class DelegatedDecision:
    decision_id: str
    issue_id: str
    kind: str
    answered: bool
    detail: str


def _delegation_allowed(grant: policy.Grant | None) -> bool:
    if grant is None:
        return False
    levels = AUTONOMY_LEVELS
    if grant.level not in levels:
        return False
    return levels.index(grant.level) >= levels.index(_MIN_DELEGATION_LEVEL)


def delegate_decisions(
    repo_root: Path,
    session: SessionState,
    *,
    beat: Callable[[], None] | None = None,
    admission: policy.SpendStatus | None = None,
) -> tuple[DelegatedDecision, ...]:

    if admission is None:
        admission = policy.spend_status(repo_root, session.root_issue)
    if not _delegation_allowed(admission.grant):
        return ()
    delegated: list[DelegatedDecision] = []
    for item in decisions.pending(repo_root, session.root_issue):
        if item.kind not in DELEGABLE_KINDS:
            continue
        if beat is not None:
            beat()
        delegated.append(_delegate_one(repo_root, item, session.root_issue))
    return tuple(delegated)


def _delegate_one(
    repo_root: Path, item: decisions.DecisionItem, root_issue: str
) -> DelegatedDecision:
    try:
        outcome = decisions.invoke_decider(repo_root, item.decision_id, root_issue)
    except (RuntimeError, OSError, ValueError) as exc:
        return DelegatedDecision(
            decision_id=item.decision_id,
            issue_id=item.issue_id,
            kind=item.kind,
            answered=False,
            detail=f"decider invocation failed: {exc}",
        )
    if isinstance(outcome, decisions.DecisionItem):
        return DelegatedDecision(
            decision_id=outcome.decision_id,
            issue_id=outcome.issue_id,
            kind=outcome.kind,
            answered=not outcome.pending,
            detail=f"{outcome.answered_by}: {outcome.answer}",
        )
    return DelegatedDecision(
        decision_id=item.decision_id,
        issue_id=item.issue_id,
        kind=item.kind,
        answered=False,
        detail=outcome.rationale or "not derivable from the corpus",
    )


def _say(report: Callable[[str], None] | None, line: str) -> None:
    if report is not None:
        report(line)


def say_delegated(delegated: tuple[DelegatedDecision, ...], say: Callable[[str], None]) -> None:
    for decided in delegated:
        verb = "decided" if decided.answered else "to human"
        say(f"decider:  {decided.decision_id} [{decided.kind}] {verb} - {decided.detail}")


def say_dispatch(
    outcomes: tuple[LaneOutcome, ...],
    *,
    carried: frozenset[str],
    admission: policy.SpendStatus,
    say: Callable[[str], None],
) -> None:

    if carried:
        say(f"carried:  {', '.join(sorted(carried))} - landing without a new dispatch")
    if admission.halted:
        say(f"halted:   {admission.detail}")
    elif not outcomes and not carried:
        say("dispatch: (no ready build-phase lanes)")
    spent = 0
    for outcome in outcomes:
        occupancy = f", context {outcome.occupancy} tokens" if outcome.occupancy is not None else ""
        note = f" [{outcome.model_note}]" if outcome.model_note else ""
        say(
            f"dispatch: {outcome.issue_id} via {outcome.runner_name}{note} - "
            f"{outcome.detail}{outcome.spend_note}{occupancy}"
        )
        spent += outcome.spend.tokens if outcome.spend is not None else 0
    if spent:
        say(f"spent:    {spent} tokens this pass, over {len(outcomes)} dispatch(es)")


def _pass_lanes(
    repo_root: Path, session: SessionState, skip: frozenset[str]
) -> tuple[AdoptedLane, ...]:

    clear_standings()
    return ready_lanes(repo_root, session, skip=skip)


def _admit_wip(
    repo_root: Path,
    session: SessionState,
    lanes: tuple[AdoptedLane, ...],
    runner_name: str,
    report: Callable[[str], None] | None,
) -> tuple[tuple[AdoptedLane, ...], tuple[LaneOutcome, ...]]:

    bound = wip.admit(repo_root, lanes, session.adopted, exclude=session.root_issue)
    _say(report, f"wip:      {bound.coverage}")
    wip.record_refusal(repo_root, session.root_issue, bound)
    land = f"; land or review {', '.join(bound.downstream)}" if bound.downstream else ""
    note_standing(LANE_REFUSED, bound.reason, *(lane.issue_id for lane in bound.refused))
    note_standing(LANE_PARKED, "unlanded work the bound is counting", *bound.downstream)
    admitted = (lane.issue_id for lane in bound.admitted)
    note_standing(LANE_QUEUED, "admitted, waiting for a runner slot", *admitted)
    held = tuple(
        _unstarted(
            lane.issue_id, runner_name, f"not started: {bound.reason}{land}", Unstarted.REFUSED
        )
        for lane in bound.refused
    )
    return bound.admitted, held


def dispatch_lanes(  # noqa: PLR0913 — each arg is one independent pass-scoped input
    repo_root: Path,
    session: SessionState,
    *,
    beat: Callable[[], None] | None = None,
    cap: int | None = None,
    skip: frozenset[str] = frozenset(),
    admission: policy.SpendStatus | None = None,
    report: Callable[[str], None] | None = None,
) -> tuple[LaneOutcome, ...]:

    lanes = _pass_lanes(repo_root, session, skip)
    if not lanes:
        return ()
    if admission is None:
        admission = policy.spend_status(repo_root, session.root_issue)
    if admission.halted:
        _say(report, f"spend:    {admission.detail}")
    if cap is None:
        cap = load_worktree_config(repo_root).concurrency
    config = load_runner_config(repo_root)
    spec = runner.select_runner(config.specs, config.default, capable=runner.is_capable)
    sizing = load_sizing_config(repo_root)

    if spec.kind == runner.HEADLESS and (
        admission.grant is None or admission.grant.token_budget is None
    ):
        _say(report, f"spend:    the {spec.name} runner is metered and no budget covers it")

    lanes, held = _admit_wip(repo_root, session, lanes, spec.name, report)
    if not lanes:
        return held

    working_sets = tuple(admit_working_set(repo_root, lane.issue_id, sizing) for lane in lanes)
    pass_spend = admit_pass_spend(repo_root, working_sets, admission, sizing)
    _report_coverage(report, repo_root, working_sets, pass_spend)
    if pass_spend.warning is not None:
        _say(report, f"spend:    {pass_spend.warning} ({pass_spend.coverage})")
    banded = {item.issue_id: item for item in working_sets}

    ranking = loop_state.ready_ranking(repo_root)
    ranked = ranking.by_issue()
    dispatch_ranks = {lane.issue_id: position for position, lane in enumerate(lanes, start=1)}

    started: dict[str, float] = {}
    gate = ProviderGate()

    def guarded(lane: AdoptedLane) -> LaneOutcome:
        if (declined := _declined_start(lane.issue_id, spec.name, gate)) is not None:
            return declined
        started[lane.issue_id] = time.monotonic()
        try:
            return gate.latch(
                _dispatch_lane(
                    repo_root,
                    session,
                    lane,
                    spec,
                    sizing,
                    ordering=DispatchOrdering(
                        dispatch_rank=dispatch_ranks.get(lane.issue_id),
                        node=ranked.get(lane.issue_id),
                        policy=ranking.schema,
                    ),
                    working_set=banded.get(lane.issue_id),
                )
            )
        except (RuntimeError, OSError, ValueError) as exc:
            return _unstarted(
                lane.issue_id,
                spec.name,
                f"lane dispatch failed: {exc}",
                Unstarted.TRANSIENT
                if tracker.is_transient_storage_error(str(exc))
                else Unstarted.STOPPED,
            )

    pool = ThreadPoolExecutor(max_workers=max(1, cap))
    try:
        futures = [pool.submit(guarded, lane) for lane in lanes]
        by_future = dict(zip(futures, lanes, strict=True))
        pending = set(futures)
        while pending:
            timeout = HEARTBEAT_INTERVAL_S if (beat or report) else None
            _done, pending = wait(pending, timeout=timeout)
            if pending and beat is not None:
                beat()
            if pending and report is not None:
                report(f"running:  {_inflight_note(started, by_future, pending)}")
    except BaseException:
        pool.shutdown(wait=False, cancel_futures=True)
        raise
    pool.shutdown(wait=True)
    return held + tuple(future.result() for future in futures)


def _inflight_note(
    started: dict[str, float],
    by_future: dict[Future[LaneOutcome], AdoptedLane],
    pending: set[Future[LaneOutcome]],
) -> str:

    now = time.monotonic()
    running = sorted(
        (lane.issue_id, now - started[lane.issue_id])
        for future, lane in by_future.items()
        if future in pending and lane.issue_id in started
    )
    if not running:
        queued = len(pending)
        return f"{queued} lane(s) queued behind the concurrency cap, none started yet"
    live = inflight_spend()
    doing = inflight_activity()
    return ", ".join(
        f"{issue_id} {elapsed:.0f}s"
        + (f" {live[issue_id]} tok" if live.get(issue_id) else "")
        + (f" [{doing[issue_id]}]" if doing.get(issue_id) else "")
        for issue_id, elapsed in running
    )


def lane_activity(cwd: Path) -> str:

    head = subprocess.run(  # noqa: S603 — argv list, no shell; see the note above
        ["git", "-C", str(cwd), "rev-parse", "HEAD"],  # noqa: S607 — PATH git, as everywhere
        capture_output=True,
        text=True,
        check=False,
    )
    dirty = subprocess.run(  # noqa: S603 — argv list, no shell; see the note above
        ["git", "-C", str(cwd), "status", "--porcelain"],  # noqa: S607 — PATH git, as everywhere
        capture_output=True,
        text=True,
        check=False,
    )
    return hashlib.sha256(f"{head.stdout}\n{dirty.stdout}".encode()).hexdigest()


class LaneStream:
    def __init__(self, *, agent: str = "", model: str = "") -> None:

        self.agent = agent
        self.model = model
        self.started_at = datetime.now(UTC).isoformat()
        self._start = time.monotonic()
        self._lock = threading.Lock()
        self._events = 0
        self._tokens = 0
        self._doing = ""

    def __call__(self, event: runner.StreamEvent) -> None:
        with self._lock:
            self._events += 1
            if event.usage is not None:
                self._tokens += event.usage.tokens
            said = _said(event)
            if said:
                self._doing = said

    @property
    def events(self) -> int:
        with self._lock:
            return self._events

    @property
    def spent(self) -> int:
        with self._lock:
            return self._tokens

    @property
    def doing(self) -> str:
        with self._lock:
            return self._doing

    @property
    def elapsed_s(self) -> float:
        return max(0.0, time.monotonic() - self._start)

    def fingerprint(self) -> str:

        return f"events:{self.events}"


_SAID_CHARS = 60


def _said(event: runner.StreamEvent) -> str:

    lines = (event.text or "").strip().splitlines()
    first = lines[0].strip() if lines else ""
    if not first:
        return ""
    clipped = first[:_SAID_CHARS].rstrip()
    if len(first) > _SAID_CHARS:
        clipped += "..."
    return f"{event.subagent}: {clipped}" if event.subagent else clipped


_LIVE_LANES: dict[str, LaneStream] = {}
_LIVE_LOCK = threading.Lock()


class _Retired:
    tokens = 0


_RETIRED = _Retired()


@contextlib.contextmanager
def live_lane(issue_id: str, stream: LaneStream) -> Iterator[LaneStream]:

    with _LIVE_LOCK:
        _LIVE_LANES[issue_id] = stream
        _STANDING.pop(issue_id, None)
    try:
        yield stream
    finally:
        final = stream.spent
        with _LIVE_LOCK:
            if _LIVE_LANES.pop(issue_id, None) is not None:
                _RETIRED.tokens += final


def inflight_spend() -> dict[str, int]:
    with _LIVE_LOCK:
        live = tuple(_LIVE_LANES.items())
    return {issue_id: stream.spent for issue_id, stream in live}


def inflight_activity() -> dict[str, str]:
    with _LIVE_LOCK:
        live = tuple(_LIVE_LANES.items())
    return {issue_id: stream.doing for issue_id, stream in live if stream.doing}


def inflight_dispatch() -> dict[str, LaneStream]:

    with _LIVE_LOCK:
        return dict(_LIVE_LANES)


LANE_LANDED = "landed"
LANE_QUEUED = "queued"
LANE_RUNNING = "running"
LANE_WAITS_TO_LAND = "waits-to-land"
LANE_LANDING = "landing"
LANE_REFUSED = "refused"
LANE_PARKED = "parked"


@dataclass(frozen=True)
class LaneStanding:
    state: str
    detail: str = ""
    since: str = ""


_STANDING: dict[str, LaneStanding] = {}


def note_standing(state: str, detail: str, *issue_ids: str) -> None:
    standing = LaneStanding(state, detail, datetime.now(UTC).isoformat())
    with _LIVE_LOCK:
        for issue_id in issue_ids:
            _STANDING[issue_id] = standing


def forget_standing(*issue_ids: str) -> None:
    with _LIVE_LOCK:
        for issue_id in issue_ids:
            _STANDING.pop(issue_id, None)


def clear_standings() -> None:
    with _LIVE_LOCK:
        _STANDING.clear()


def lane_standings() -> dict[str, LaneStanding]:
    with _LIVE_LOCK:
        return dict(_STANDING)


LIVE_OVERREPORT_BOUND = 2.0


STALL_FLAG_QUESTION = "lane may be stuck: intervene now or let the hard kill arrive?"


def flag_stalled_lane(
    repo_root: Path, issue_id: str, stall_after: float, quiet_after: float
) -> decisions.DecisionItem:

    return decisions.enqueue(
        repo_root,
        issue_id,
        "stall",
        STALL_FLAG_QUESTION,
        f"no commits and no file changes for {stall_after:g}s; the run continues "
        f"until the quiet bound ({quiet_after:g}s), still holding a lane slot",
    )


def resolve_stall_flag(repo_root: Path, issue_id: str) -> tuple[str, ...]:

    disposed: list[str] = []
    for item in decisions.items_on(repo_root, issue_id):
        if item.kind == "stall" and item.question == STALL_FLAG_QUESTION and item.pending:
            decisions.answer(
                repo_root,
                item.decision_id,
                "dispatch ended; nothing left to intervene in before a hard kill",
                by=decisions.ENGINE_BY,
            )
            disposed.append(item.decision_id)
    return tuple(disposed)


@dataclass(frozen=True)
class DispatchOrdering:
    dispatch_rank: int | None
    node: loop_state.RankedNode | None
    policy: str

    def as_inputs(self) -> dict[str, object]:
        return {
            "dispatch_rank": self.dispatch_rank,
            "scheduler_rank": self.node.rank if self.node else None,
            "scheduler_fallback_rank": self.node.fallback_rank if self.node else None,
            "scheduler_score": self.node.score if self.node else None,
            "scheduler_policy": self.policy or None,
        }


def record_unstarted_dispatch(
    repo_root: Path, issue_id: str, spec: runner.RunnerSpec, error: BaseException
) -> None:

    runner.record_dispatch(
        repo_root,
        issue_id,
        spec,
        runner.RunResult(spec.name, (), executed=False, stderr=redact_secrets(str(error))),
        phase=run_record.LANE_PHASE,
    )


def _lane_seed(
    repo_root: Path, root_issue: str, spec: runner.RunnerSpec
) -> runner.SessionSeed | None:

    if spec.resume_style is None or not roles.phase_inherits_context("build"):
        return None
    return runner.session_seed(repo_root, root_issue, runner.model_family(spec))


def _keep_lane_seed(
    repo_root: Path,
    root_issue: str,
    spec: runner.RunnerSpec,
    seed: runner.SessionSeed | None,
    result: runner.RunResult,
) -> None:

    if seed is not None and not seed.exists and result.returncode == 0:
        runner.record_session_seed(
            repo_root, root_issue, runner.model_family(spec), seed.session_id
        )


def _finished_detail(
    result: runner.RunResult,
    refused: provider_limit.LimitRefusal | None,
    needs: needs_input.NeedsInput | None,
    verdict: context_meter.CeilingVerdict,
) -> str:

    if refused is not None:
        detail = refused.detail
    elif result.returncode != 0:
        detail = f"runner exited {result.returncode}"
    elif needs is not None:
        detail = f"needs input: {needs.detail or needs.fact}"
    else:
        detail = "finished; ready to land"
    return f"{detail}; {verdict.observation}" if verdict.overrun else detail


def _dispatch_lane(  # noqa: PLR0913 — one parameter per independent lane input
    repo_root: Path,
    session: SessionState,
    lane: AdoptedLane,
    spec: runner.RunnerSpec,
    sizing: SizingConfig,
    ordering: DispatchOrdering | None = None,
    working_set: WorkingSetAdmission | None = None,
) -> LaneOutcome:

    record = worktree.load_session(lane.binding.name, repo_root)
    if record is None:
        note_standing(
            LANE_REFUSED, f"worktree {lane.binding.name!r} has no session record", lane.issue_id
        )
        return _unstarted(
            lane.issue_id,
            spec.name,
            f"worktree {lane.binding.name!r} has no session record; re-provision the lane",
            Unstarted.STOPPED,
        )
    admission = working_set
    if admission is None:
        admission = admit_working_set(repo_root, lane.issue_id, sizing)
    queued = escalate_working_set(repo_root, admission)
    if admission.refused:
        held = f"; held by {queued.decision_id}" if queued is not None else ""
        note_standing(LANE_REFUSED, f"{admission.violation}{held}", lane.issue_id)
        return _unstarted(
            lane.issue_id,
            spec.name,
            f"dispatch refused before it started: {admission.violation}{held}",
            Unstarted.REFUSED,
        )
    lane_sizing = admission.record_inputs(repo_root)
    if not lane_sizing:
        assumed_tokens, assumed_source = decompose.unsized_lane_tokens(repo_root, sizing)
        lane_sizing = {
            "forecast_spend_tokens": assumed_tokens,
            "forecast_source": f"assumed:{assumed_source}",
        }
    known = frozenset({session.root_issue, *(cid for cid, _ in session.children)})
    try:
        cwd = Path(record.worktree_path)
        bundle = build_bundle(repo_root, lane.issue_id, known_ids=known, cwd=cwd)
        runner_config = load_runner_config(repo_root)
        seed = _lane_seed(repo_root, session.root_issue, spec)
        stream = LaneStream(
            agent=spec.name, model=runner.resolve_model(spec, repo_root=cwd).model or ""
        )
        watchdog = runner.StallWatchdog(
            runner_config.stall_after,
            probe=lambda: f"{stream.fingerprint()} {lane_activity(cwd)}",
            on_stall=lambda: flag_stalled_lane(
                repo_root, lane.issue_id, runner_config.stall_after, runner_config.quiet_after
            ),
        )
        bounds = runner.DispatchBounds(
            quiet_after=runner_config.quiet_after,
            token_ceiling=runner_config.lane_token_ceiling or None,
        )
        with (
            live_lane(lane.issue_id, stream),
            lane_log.lane_transcript(repo_root, session.log_session, lane.issue_id) as transcript,
            runner.process_budget().slot(runner.LANE),
            watchdog,
        ):
            result = runner.run(
                spec,
                bundle.prompt,
                cwd,
                capture_usage=True,
                timeout=runner_config.runner_timeout,
                on_event=lane_log.fanout(stream, transcript),
                bounds=bounds,
                role=roles.resolve_role(repo_root, spec, "build"),
                seed=seed,
            )
        _keep_lane_seed(repo_root, session.root_issue, spec, seed, result)
    except (RuntimeError, OSError, ValueError) as exc:
        record_unstarted_dispatch(repo_root, lane.issue_id, spec, exc)
        raise
    loop.record_run(
        repo_root,
        lane.issue_id,
        spec,
        result,
        prompt=bundle.prompt,
        phase=run_record.LANE_PHASE,
        stopped_bound=result.stopped.bound if result.stopped is not None else None,
        folded_info=tuple(_folded_ref(info) for info in bundle.folded),
        **lane_sizing,
        **(ordering.as_inputs() if ordering else {}),
    )
    resolve_stall_flag(repo_root, lane.issue_id)
    if result.timed_out:
        bound = runner.stop_label(result, runner_config.runner_timeout)
        stale_needs = needs_input.take(cwd)
        salvaged = commit.salvage(cwd, lane.issue_id, reason=bound)
        stall = decisions.enqueue(
            repo_root,
            lane.issue_id,
            "stall",
            f"runner {spec.name} stopped on {bound}: retry, re-dispatch, or park?",
            "; ".join(
                part
                for part in (
                    salvaged.detail,
                    stale_needs.fact if stale_needs is not None else "",
                )
                if part
            ),
        )
        return LaneOutcome(
            issue_id=lane.issue_id,
            runner_name=spec.name,
            result=result,
            needs_fact=None,
            occupancy=None,
            overrun=False,
            salvaged=salvaged.committed,
            detail=f"stopped on {bound}; {salvaged.detail}; stall queued as {stall.decision_id}",
        )
    if result.handoff:
        return LaneOutcome(
            issue_id=lane.issue_id,
            runner_name=spec.name,
            result=result,
            needs_fact=None,
            occupancy=None,
            overrun=False,
            detail="handoff runner: work left to the driving agent",
        )
    needs = needs_input.take(cwd)
    if needs is not None:
        policy.record_needs_input(repo_root, lane.issue_id, needs.fact)
        decisions.enqueue(repo_root, lane.issue_id, "needs-input", needs.fact, needs.detail)
    verdict = context_meter.meter_context_ceiling(spec, result, sizing)
    refused = provider_limit.refusal(spec.usage_format, result.stdout)
    detail = _finished_detail(result, refused, needs, verdict)
    resolution = result.model_resolution
    return LaneOutcome(
        issue_id=lane.issue_id,
        runner_name=spec.name,
        result=result,
        needs_fact=needs.fact if needs is not None else None,
        occupancy=verdict.occupancy,
        overrun=verdict.overrun,
        detail=detail,
        model=resolution.model if resolution is not None else spec.model,
        model_tier=resolution.tier if resolution is not None else None,
        model_source=resolution.source if resolution is not None else None,
        observed_models=runner.observed_models(spec, result),
        tier_honoured=resolution.honoured if resolution is not None else None,
        provider_refusal=refused.said if refused is not None else "",
        spend=runner.extract_usage(spec, result),
    )


DISPATCH_GATE = "dispatch"
TRACKER_GATE = "tracker-storage"


READY_TO_LAND = "ready-to-land"

RETRIABLE_ROUTES = (
    "retry",
    "rework",
    "held",
    "lane-step",
    "bounced",
    "re-dispatch",
    "repaired",
    READY_TO_LAND,
    "seeded",
)


@dataclass(frozen=True)
class RoutedOutcome:
    issue_id: str
    route: str
    detail: str

    @property
    def progressed(self) -> bool:
        return self.route in ("merged", "shipped")


def should_continue(routed: tuple[RoutedOutcome, ...]) -> bool:

    return any(r.progressed or r.route in RETRIABLE_ROUTES for r in routed)


def carried_forward(routed: tuple[RoutedOutcome, ...]) -> frozenset[str]:

    return frozenset(r.issue_id for r in routed if r.route in ("held", READY_TO_LAND))


def _awaits_landing(repo_root: Path, lane: AdoptedLane) -> bool:

    session = worktree.load_session(lane.binding.name, repo_root)
    if session is None or session.stale:
        return False
    if (session.path / repair_brief.REPAIR_BRIEF_FILE).is_file():
        return False
    if not merge.carried_commits(repo_root, session.base, session.branch):
        return False
    dirty = worktree.git(
        ["status", "--porcelain", "--untracked-files=no"], cwd=session.path, check=False
    )
    return dirty.returncode == 0 and not dirty.stdout.strip()


def committed_lanes(repo_root: Path, session: SessionState) -> frozenset[str]:

    if not session.adopted:
        return frozenset()
    return frozenset(
        lane.issue_id
        for lane in ready_lanes(repo_root, session)
        if _awaits_landing(repo_root, lane)
    )


def _carried_outcome(issue_id: str) -> LaneOutcome:

    return _unstarted(
        issue_id,
        "(none)",
        "work already committed on the branch; landing without a fresh dispatch",
        Unstarted.CARRIED,
    )


def route_outcomes(
    repo_root: Path,
    session: SessionState,
    outcomes: tuple[LaneOutcome, ...],
    *,
    beat: Callable[[], None] | None = None,
    carried: Iterable[str] = (),
) -> tuple[RoutedOutcome, ...]:

    pass_outcomes = _carried_outcomes(repo_root, session, carried, outcomes) + outcomes
    ordered = _landing_order(repo_root, pass_outcomes)
    queue = _note_landing_queue(ordered)
    try:
        return _land_in_order(repo_root, session, ordered, beat)
    finally:
        forget_standing(*queue)


def _note_landing_queue(ordered: Sequence[LaneOutcome]) -> tuple[str, ...]:

    waiting = [one.issue_id for one in ordered if _is_green(one) or one.salvaged]
    for position, issue_id in enumerate(waiting, start=1):
        note_standing(
            LANE_WAITS_TO_LAND, f"{position} of {len(waiting)} in the landing queue", issue_id
        )
    return tuple(waiting)


def _land_in_order(
    repo_root: Path,
    session: SessionState,
    ordered: Sequence[LaneOutcome],
    beat: Callable[[], None] | None,
) -> tuple[RoutedOutcome, ...]:

    routed: list[RoutedOutcome] = []
    landing_blocked = False
    landed: list[tuple[str, tuple[str, ...]]] = []
    collisions: list[tuple[str, tuple[str, ...]]] = []
    for outcome in ordered:
        if beat is not None:
            beat()
        lands = _is_green(outcome) or outcome.salvaged
        if landing_blocked and lands:
            routed.append(
                RoutedOutcome(
                    outcome.issue_id,
                    "held",
                    "landing paused after an earlier failure this pass",
                )
            )
            continue
        before = merge.head_sha(repo_root) if lands else ""
        try:
            if lands:
                note_standing(LANE_LANDING, "the supervisor is landing this lane", outcome.issue_id)
            one = _route_one(repo_root, session, outcome, landed, collisions)
        except merge.TrackerCommitRefusedError as exc:
            one = RoutedOutcome(
                outcome.issue_id,
                READY_TO_LAND,
                f"landing deferred: the engine's own tracker-sync commit was refused: {exc}",
            )
        except (RuntimeError, OSError, ValueError) as exc:
            one = RoutedOutcome(outcome.issue_id, "error", f"routing failed: {exc}")
        if lands:
            if one.progressed:
                forget_standing(outcome.issue_id)
            elif one.route in ("held", READY_TO_LAND):
                note_standing(LANE_WAITS_TO_LAND, one.detail, outcome.issue_id)
            else:
                note_standing(LANE_REFUSED, one.detail or one.route, outcome.issue_id)
        routed.append(one)
        if one.progressed:
            landed.append((outcome.issue_id, merge.changed_paths(repo_root, before)))
        elif lands and one.route not in ("bounced", "re-dispatch", READY_TO_LAND):
            landing_blocked = True
    return _attribute_pass_couplings(repo_root, tuple(routed), collisions, landed)


def _attribute_pass_couplings(
    repo_root: Path,
    routed: tuple[RoutedOutcome, ...],
    collisions: list[tuple[str, tuple[str, ...]]],
    landed: list[tuple[str, tuple[str, ...]]],
) -> tuple[RoutedOutcome, ...]:

    if not collisions:
        return routed
    try:
        attributed = merge.record_pass_couplings(
            repo_root, collisions, [bead for bead, _ in landed]
        )
    except RuntimeError, OSError, ValueError:
        attributed = {}
    _record_bounce_briefs(repo_root, collisions, attributed)
    return tuple(_reporting_couplings(one, attributed.get(one.issue_id, ())) for one in routed)


def _record_bounce_briefs(
    repo_root: Path,
    collisions: list[tuple[str, tuple[str, ...]]],
    attributed: dict[str, tuple[str, ...]],
) -> None:

    for bead, conflicts in collisions:
        paths = ", ".join(conflicts) or "paths git did not name"
        culprits = attributed.get(bead, ())
        who = ", ".join(culprits) if culprits else "another lane"
        try:
            record_found_info(
                repo_root,
                bead,
                FoundInfo(
                    kind="coupling",
                    summary=(
                        f"{paths}: this branch no longer rebases onto its base, because "
                        f"{who} landed over those paths"
                    ),
                    detail=(
                        "your side is the commits already on this lane's branch; the "
                        "other side is those paths as they now stand on the base. "
                        "Resolve each conflicting path against both sides and commit on "
                        "this branch — the work itself is done, do not redo it."
                    ),
                    affects=(bead,),
                ),
            )
        except RuntimeError, OSError, ValueError:
            continue


def _reporting_couplings(one: RoutedOutcome, culprits: tuple[str, ...]) -> RoutedOutcome:
    if not culprits:
        return one
    return RoutedOutcome(
        one.issue_id, one.route, f"{one.detail}; coupling recorded on {', '.join(culprits)}"
    )


def _carried_outcomes(
    repo_root: Path,
    session: SessionState,
    carried: Iterable[str],
    outcomes: tuple[LaneOutcome, ...],
) -> tuple[LaneOutcome, ...]:

    wanted = frozenset(carried) - {outcome.issue_id for outcome in outcomes}
    if not wanted:
        return ()
    eligible = {lane.issue_id for lane in ready_lanes(repo_root, session)}
    return tuple(_carried_outcome(issue_id) for issue_id in sorted(wanted & eligible))


def _landing_order(repo_root: Path, outcomes: tuple[LaneOutcome, ...]) -> list[LaneOutcome]:

    by_id = {outcome.issue_id: outcome for outcome in outcomes}
    items = [(outcome.issue_id, outcome.issue_id) for outcome in outcomes]
    return [by_id[bead] for _, bead in merge.landing_order(repo_root, items)]


def _is_green(outcome: LaneOutcome) -> bool:
    if not outcome.dispatched:
        return True
    result = outcome.result
    return (
        result is not None
        and result.executed
        and result.returncode == 0
        and not result.timed_out
        and not result.handoff
        and outcome.needs_fact is None
    )


def _seeding_declined(
    repo_root: Path,
    session: SessionState,
    *,
    skip: frozenset[str],
    admission: policy.SpendStatus | None,
) -> tuple[RoutedOutcome, ...] | None:

    if ready_lanes(repo_root, session, skip=skip):
        return ()
    if not session.open_children:
        leaf = not session.children and loop_state.is_dispatchable(session.root_status)
        if not leaf:
            return ()
    if admission is None:
        return None
    blocked_runner = metered_without_a_budget(repo_root, admission)
    if blocked_runner is None:
        return None
    return (
        RoutedOutcome(
            session.root_issue,
            "seed-blocked",
            f"not provisioning lanes for the {blocked_runner!r} runner: "
            "no grant with a token budget covers this session",
        ),
    )


def seed_lanes(
    repo_root: Path,
    session: SessionState,
    *,
    skip: frozenset[str] = frozenset(),
    admission: policy.SpendStatus | None = None,
) -> tuple[RoutedOutcome, ...]:

    declined = _seeding_declined(repo_root, session, skip=skip, admission=admission)
    if declined is not None:
        return declined
    if session.lane_label is not None:
        return _seed_selected_lanes(repo_root, session, skip=skip)
    try:
        ceremony = loop.run_ceremony(repo_root, session.root_issue, grant_root=session.root_issue)
    except (RuntimeError, OSError, ValueError) as exc:
        return (RoutedOutcome(session.root_issue, "error", f"seeding the root failed: {exc}"),)
    steps = list(ceremony.steps)
    if not steps:
        return ()
    return _seeding_outcome(repo_root, session, steps, skip=skip, ceremony=ceremony)


def _seed_selected_lanes(
    repo_root: Path, session: SessionState, *, skip: frozenset[str]
) -> tuple[RoutedOutcome, ...]:

    lanes = tuple(
        (issue_id, status)
        for issue_id, status in session.children
        if loop_state.is_dispatchable(status)
    )
    try:
        gained = loop.ensure_lane_worktrees(repo_root, session.root_issue, lanes)
    except (RuntimeError, OSError, ValueError) as exc:
        return (
            RoutedOutcome(session.root_issue, "error", f"seeding the selected lanes failed: {exc}"),
        )
    derived = derive_session(repo_root, session.root_issue, lane_label=session.lane_label)
    dispatchable = ready_lanes(repo_root, derived, skip=skip)
    selected = f"{len(lanes)} lane(s) selected by label {session.lane_label!r}"
    if dispatchable:
        return (
            RoutedOutcome(
                session.root_issue,
                "seeded",
                f"provisioned {len(gained)} of {selected}, {len(dispatchable)} dispatchable",
            ),
        )
    if gained:
        return (
            RoutedOutcome(
                session.root_issue,
                "seed-blocked",
                f"provisioned {len(gained)} lane(s) but none is dispatchable ({', '.join(gained)})",
            ),
        )
    return (
        RoutedOutcome(
            session.root_issue,
            "seed-blocked",
            f"no lane could be provisioned from {selected}",
        ),
    )


def _seeding_outcome(
    repo_root: Path,
    session: SessionState,
    steps: list[loop.AdvanceResult],
    *,
    skip: frozenset[str],
    ceremony: loop.CeremonyResult | None = None,
) -> tuple[RoutedOutcome, ...]:

    final = steps[-1]
    live_before = frozenset(lane.issue_id for lane in session.adopted if lane.live)
    derived = derive_session(repo_root, session.root_issue, lane_label=session.lane_label)
    dispatchable = ready_lanes(repo_root, derived, skip=skip)
    if any(step.progressed for step in steps) or dispatchable:
        detail = final.detail
        if dispatchable:
            detail = f"provisioned {len(dispatchable)} dispatchable lane(s) - {detail}"
        return (RoutedOutcome(session.root_issue, "seeded", detail),)
    gained = frozenset(lane.issue_id for lane in derived.adopted if lane.live) - live_before
    if gained:
        return (
            RoutedOutcome(
                session.root_issue,
                "seed-blocked",
                f"provisioned {len(gained)} lane(s) but none is dispatchable "
                f"({', '.join(sorted(gained))}) - {final.detail}",
            ),
        )
    unauthorized = _unauthorized_detail(ceremony, session.root_issue)
    return (
        RoutedOutcome(
            session.root_issue,
            "seed-blocked",
            f"no lane could be provisioned from {len(session.open_children)} open "
            f"child(ren) - {final.detail}{unauthorized}",
        ),
    )


def _unauthorized_detail(ceremony: loop.CeremonyResult | None, root_issue: str) -> str:

    if ceremony is None:
        return ""
    if ceremony.refused is not None:
        name, why = ceremony.refused
        return f"; the {name} checkpoint refused: {why}"
    if ceremony.challenge is None:
        return ""
    name, _code = ceremony.challenge
    if ceremony.challenge_reason:
        return f"; the {name} checkpoint was not delegated: {ceremony.challenge_reason}"
    covering = next((level for level, names in policy.GRANT_COVERAGE.items() if name in names), "")
    if not covering:
        return f"; no autonomy level delegates the {name} checkpoint, so it needs a human"
    return (
        f"; no grant covers the {name} checkpoint - an {covering} grant delegates it: "
        f"basicly policy grant {root_issue} --level {covering}"
    )


def repair_stale_bindings(repo_root: Path, session: SessionState) -> tuple[RoutedOutcome, ...]:

    routed: list[RoutedOutcome] = []
    for lane in session.adopted:
        if lane.live:
            continue
        clearable, detail = loop.stale_binding_verdict(repo_root, lane.binding)
        if not clearable:
            decisions.enqueue(
                repo_root,
                lane.issue_id,
                "escalation",
                "a worktree binding outlived its worktree and its branch is unlanded: "
                "merge the branch, delete it, or clear the binding?",
                detail,
            )
            routed.append(RoutedOutcome(lane.issue_id, "decision", detail))
            continue
        loop.clear_worktree_binding(repo_root, lane.issue_id)
        routed.append(RoutedOutcome(lane.issue_id, "repaired", detail))
    return tuple(routed)


def advance_parked(
    repo_root: Path, session: SessionState, *, beat: Callable[[], None] | None = None
) -> tuple[RoutedOutcome, ...]:

    routed: list[RoutedOutcome] = []
    for lane in session.adopted:
        if not lane.live or decisions.has_pending(repo_root, lane.issue_id):
            continue
        if beat is not None:
            beat()
        try:
            phase = _phase_of(repo_root, lane.issue_id)
            mini_loop = phase == "build" and _has_subtasks(repo_root, lane.issue_id)
            if phase not in wip.DOWNSTREAM_PHASES and not mini_loop:
                continue
            steps = loop.run_until_blocked(repo_root, lane.issue_id, grant_root=session.root_issue)
        except (RuntimeError, OSError, ValueError) as exc:
            routed.append(
                RoutedOutcome(lane.issue_id, "error", f"advancing parked lane failed: {exc}")
            )
            continue
        final = steps[-1] if steps else None
        if final is None:
            continue
        if final.to_phase == "done":
            routed.append(RoutedOutcome(lane.issue_id, "shipped", final.detail))
        elif any(step.progressed for step in steps):
            route = "lane-step" if final.to_phase == "build" else "merged"
            routed.append(RoutedOutcome(lane.issue_id, route, final.detail))
        else:
            routed.append(
                RoutedOutcome(
                    lane.issue_id, "lane-blocked", _blocked_lane_detail(repo_root, lane, final)
                )
            )
    return tuple(routed)


def _blocked_lane_detail(repo_root: Path, lane: AdoptedLane, final: loop.AdvanceResult) -> str:

    with contextlib.suppress(OSError, RuntimeError, ValueError):
        decisions.enqueue(
            repo_root,
            lane.issue_id,
            "escalation",
            PARKED_LANE_QUESTION,
            f"{final.to_phase}: {final.detail}",
        )
    return final.detail


def _route_one(
    repo_root: Path,
    session: SessionState,
    outcome: LaneOutcome,
    landed: list[tuple[str, tuple[str, ...]]],
    collisions: list[tuple[str, tuple[str, ...]]],
) -> RoutedOutcome:

    issue_id = outcome.issue_id
    result = outcome.result
    if not outcome.dispatched:
        return _land_green(repo_root, session, outcome, landed, collisions)
    if result is not None and result.handoff:
        return RoutedOutcome(issue_id, "handoff", outcome.detail)
    if outcome.refused or outcome.needs_fact is not None:
        return RoutedOutcome(issue_id, "decision", outcome.detail)
    if result is not None and result.timed_out:
        return _route_timeout(repo_root, session, outcome, landed, collisions)
    if result is None or result.returncode != 0:
        return _route_failed(repo_root, issue_id, outcome)
    return _land_green(repo_root, session, outcome, landed, collisions)


def _route_timeout(
    repo_root: Path,
    session: SessionState,
    outcome: LaneOutcome,
    landed: list[tuple[str, tuple[str, ...]]],
    collisions: list[tuple[str, tuple[str, ...]]],
) -> RoutedOutcome:

    if not outcome.salvaged:
        return RoutedOutcome(outcome.issue_id, "decision", outcome.detail)
    return _land_green(repo_root, session, outcome, landed, collisions)


def _route_blocked_landing(
    repo_root: Path,
    outcome: LaneOutcome,
    landing: loop.AdvanceResult,
    collisions: list[tuple[str, tuple[str, ...]]],
) -> RoutedOutcome:

    attempt = landing.landing
    if attempt is not None and attempt.conflicted:
        return _bounce_lane(repo_root, outcome.issue_id, landing, attempt, collisions)
    if landing.action == "escalated":
        return RoutedOutcome(outcome.issue_id, "decision", landing.detail)
    if attempt is not None and attempt.foreign:
        return RoutedOutcome(outcome.issue_id, "held", landing.detail)
    if attempt is not None and attempt.unreliable:
        return RoutedOutcome(outcome.issue_id, "held", landing.detail)
    if attempt is not None and attempt.status == "not-ready":
        return _route_failed(repo_root, outcome.issue_id, outcome)
    return RoutedOutcome(outcome.issue_id, "rework", landing.detail)


MAX_REPEAT_BOUNCES = 1


def conflict_signature(attempt: merge.MergeResult) -> tuple[str, ...]:

    return policy.finding_signature((f"status={attempt.status}", *attempt.conflicts))


def _bounce_convergence(
    repo_root: Path, issue_id: str, attempt: merge.MergeResult
) -> policy.Convergence | None:

    try:
        return policy.record_finding_set(
            repo_root, issue_id, merge.MERGE_GATE, conflict_signature(attempt)
        )
    except RuntimeError, OSError, ValueError:
        return None


def _bounce_lane(
    repo_root: Path,
    issue_id: str,
    landing: loop.AdvanceResult,
    attempt: merge.MergeResult,
    collisions: list[tuple[str, tuple[str, ...]]],
) -> RoutedOutcome:

    collisions.append((issue_id, attempt.conflicts))
    convergence = _bounce_convergence(repo_root, issue_id, attempt)
    if convergence is not None and convergence.stalled_rounds >= MAX_REPEAT_BOUNCES:
        return _escalate_repeat_bounce(repo_root, issue_id, convergence)
    route = "decision" if landing.action == "escalated" else "bounced"
    return RoutedOutcome(issue_id, route, f"bounced back to the lane: {landing.detail}")


def _escalate_repeat_bounce(
    repo_root: Path, issue_id: str, convergence: policy.Convergence
) -> RoutedOutcome:

    signature = " ".join(convergence.members)
    policy.spend_convergence_refund(repo_root, issue_id, merge.MERGE_GATE)
    item = decisions.enqueue(
        repo_root,
        issue_id,
        policy.REWORK_ESCALATION_KIND,
        policy.rework_escalation_question(merge.MERGE_GATE),
        (
            f"the landing failed identically to the previous attempt ({signature}); "
            "re-applying this branch to the same anchor cannot converge — re-scope it, "
            "serialize it, or resolve the conflict by hand"
        ),
    )
    return RoutedOutcome(
        issue_id,
        "decision",
        (
            f"bounced identically twice on {signature}; escalated without charging "
            f"rework ({item.decision_id})"
        ),
    )


def _route_failed(repo_root: Path, issue_id: str, outcome: LaneOutcome) -> RoutedOutcome:

    if outcome.provider_refusal:
        return _route_provider_limit(repo_root, issue_id, outcome)
    if outcome.transient:
        return _capped_dispatch(
            repo_root,
            issue_id,
            route="retry",
            detail=outcome.detail,
            question="the tracker's storage kept failing this dispatch: retry or park?",
            gate=TRACKER_GATE,
        )
    return _capped_dispatch(
        repo_root,
        issue_id,
        route="retry",
        detail=outcome.detail,
        question="dispatch failed at the rework cap: retry, re-dispatch, or park?",
    )


def _route_provider_limit(repo_root: Path, issue_id: str, outcome: LaneOutcome) -> RoutedOutcome:

    item = decisions.enqueue(
        repo_root,
        issue_id,
        "escalation",
        provider_limit.LIMIT_QUESTION,
        outcome.detail,
    )
    return RoutedOutcome(issue_id, "decision", f"{outcome.detail}; held by {item.decision_id}")


def _capped_dispatch(  # noqa: PLR0913 — route, detail and question vary independently
    repo_root: Path,
    issue_id: str,
    *,
    route: str,
    detail: str,
    question: str,
    gate: str = DISPATCH_GATE,
) -> RoutedOutcome:

    config = policy.load_policy(repo_root)
    attempts = policy.record_rework(repo_root, issue_id, gate)
    if attempts < config.max_rework:
        return RoutedOutcome(
            issue_id, route, f"{detail} ({gate} rework {attempts}/{config.max_rework})"
        )
    item = decisions.enqueue(repo_root, issue_id, "escalation", question, detail)
    return RoutedOutcome(issue_id, "decision", f"{detail}; escalated as {item.decision_id}")


def _invalidated_by(
    repo_root: Path,
    session: SessionState,
    issue_id: str,
    landed: list[tuple[str, tuple[str, ...]]],
) -> tuple[str, ...]:

    if not landed:
        return ()
    lane = next((la for la in session.adopted if la.issue_id == issue_id and la.live), None)
    if lane is None:
        return ()
    try:
        record = worktree.load_session(lane.binding.name, repo_root)
        if record is None:
            return ()
        probe = merge.probe_merge(repo_root, record.base, record.branch)
    except RuntimeError, OSError, ValueError:
        return ()
    if probe.safe:
        return ()
    return merge.missed_couplings(probe.conflicts, landed)


def _preempt_lane(repo_root: Path, issue_id: str, culprits: tuple[str, ...]) -> RoutedOutcome:

    who = ", ".join(culprits)
    record_found_info(
        repo_root,
        issue_id,
        FoundInfo(
            kind="coupling",
            summary=f"{who} landed changes this branch no longer merges cleanly onto",
            detail=(
                "the supervisor cancelled this lane's landing rather than let it "
                "collide; re-apply your intent on top of what those lanes landed"
            ),
            affects=(issue_id,),
        ),
    )
    return _capped_dispatch(
        repo_root,
        issue_id,
        route="re-dispatch",
        detail=(
            f"cancelled before landing: {who} landed changes this branch no longer "
            "merges onto; recorded for the next dispatch"
        ),
        question=(
            "a landing keeps breaking this lane's merge at the rework cap: "
            "re-scope it, serialize it, or park?"
        ),
    )


def _land_green(
    repo_root: Path,
    session: SessionState,
    outcome: LaneOutcome,
    landed: list[tuple[str, tuple[str, ...]]],
    collisions: list[tuple[str, tuple[str, ...]]],
) -> RoutedOutcome:

    invalidated = _invalidated_by(repo_root, session, outcome.issue_id, landed)
    if invalidated:
        return _preempt_lane(repo_root, outcome.issue_id, invalidated)
    landing = loop.advance(repo_root, outcome.issue_id, repair_dispatch=False)
    if landing.blocked:
        return _route_blocked_landing(repo_root, outcome, landing, collisions)
    approval = policy.approve_checkpoint_guarded(
        repo_root,
        outcome.issue_id,
        "ship",
        interactive=False,
        grant_root=session.root_issue,
    )
    if approval.status != "approved":
        item = decisions.enqueue(
            repo_root,
            outcome.issue_id,
            "checkpoint",
            f"approve the ship checkpoint for {outcome.issue_id}",
            "; ".join(part for part in (landing.detail, approval.detail) if part),
        )
        return RoutedOutcome(
            outcome.issue_id,
            "merged",
            f"landed; ship awaits a human ({item.decision_id})",
        )
    shipped = loop.run_until_blocked(repo_root, outcome.issue_id, grant_root=session.root_issue)
    final = shipped[-1] if shipped else landing
    if final.to_phase == "done":
        return RoutedOutcome(outcome.issue_id, "shipped", final.detail)
    return RoutedOutcome(outcome.issue_id, "merged", final.detail)
