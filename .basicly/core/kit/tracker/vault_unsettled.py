from __future__ import annotations

import importlib.util
import json
import math
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent


def _load(name: str) -> Any:
    key = "basicly_tracker_kit_" + name
    if key in sys.modules:
        return sys.modules[key]
    spec = importlib.util.spec_from_file_location(key, _HERE / (name + ".py"))
    if spec is None or spec.loader is None:
        raise ImportError("The unsettled-claim support is missing; reinstall the tracker kit.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[key] = module
    spec.loader.exec_module(module)
    return module


claim = _load("vault_claim")
queue = claim.vault_queue
clone_api = claim.vault_clone
share = claim.vault_share
events = claim.events
Clock = Callable[[], float]
EXPIRY = 600.0
CANCELLED = "cancelled_claim"
_CANCEL_PENDING = (
    events.LockUnavailableError,
    share.DeadlineError,
    claim.git_runner.GitTimeoutError,
)
_STATE_REFUSAL = (OSError, ValueError, claim.git_runner.GitRunnerError)
_WAIT_RETRY = (events.LockUnavailableError, share.DeadlineError, claim.git_runner.GitRunnerError)
_INVALID_QUEUE = (OSError, ValueError)


def _invalid(_value: str) -> None:
    raise queue.QueueError("The pending queue is invalid; preserve it and run fsck --repair.")


def _read(clone: Any, *, remaining: Clock = lambda: events.DEFAULT_LOCK_TIMEOUT_S) -> list[dict]:
    path = clone.folder / queue.QUEUE_NAME
    with events.LedgerLock(clone.folder, timeout_s=remaining()):
        raw = path.read_bytes() if path.exists() else b""
    try:
        text = raw.decode("utf-8")
        found = [json.loads(line, parse_constant=_invalid) for line in text.splitlines()]
    except (ValueError, UnicodeError) as exc:
        raise queue.QueueError(
            "The pending queue is invalid; preserve it and run fsck --repair."
        ) from exc
    if any(not isinstance(line, dict) for line in found):
        _invalid("")
    return found


def _claims(clone: Any, *, remaining: Clock = lambda: events.DEFAULT_LOCK_TIMEOUT_S) -> list[dict]:
    return _claim_lines(_read(clone, remaining=remaining))


def _claim_lines(lines: list[dict]) -> list[dict]:
    found = [line for line in lines if line.get("kind") == "claim"]
    for line in found:
        stamp = line.get("at")
        if (
            not isinstance(line.get("id"), str)
            or not line["id"]
            or not isinstance(line.get("record"), str)
            or not line["record"]
            or not isinstance(line.get("session"), str)
            or not line["session"]
            or not isinstance(stamp, (int, float))
            or isinstance(stamp, bool)
            or not math.isfinite(stamp)
        ):
            _invalid("")
    return found


def _release_lines(lines: list[dict]) -> list[dict]:
    found = [
        line
        for line in lines
        if isinstance(line.get("payload"), dict) and CANCELLED in line["payload"]
    ]
    if any(
        line.get("kind") != "unassign"
        or not isinstance(line["payload"][CANCELLED], str)
        or not line["payload"][CANCELLED]
        for line in found
    ):
        _invalid("")
    return found


def validate_lines(lines: list[dict]) -> None:
    _claim_lines(lines)
    _release_lines(lines)


def validate_pending(clone: Any, remaining: Clock) -> None:
    if has_pending(clone):
        validate_lines(_read(clone, remaining=remaining))


def has_pending(clone: Any) -> bool:
    if not (clone.folder / queue.QUEUE_NAME).exists():
        return False
    return any(
        line.get("kind") == "claim"
        or (isinstance(line.get("payload"), dict) and CANCELLED in line["payload"])
        for line in queue.pending(clone.folder / queue.QUEUE_NAME)
    )


def _drop(clone: Any, identities: set[str], *, remaining: Clock) -> None:
    path = clone.folder / queue.QUEUE_NAME
    with events.LedgerLock(clone.folder, timeout_s=remaining()):
        text = path.read_bytes().decode("utf-8") if path.exists() else ""
        keep = []
        for raw in text.splitlines(keepends=True):
            try:
                line = json.loads(raw, parse_constant=_invalid)
            except ValueError as exc:
                raise queue.QueueError(
                    "The pending queue is invalid; preserve it and run fsck --repair."
                ) from exc
            if not isinstance(line, dict):
                _invalid("")
            if line.get("id") not in identities:
                keep.append(raw)
        clone_api.durable(path, "".join(keep))


def _fences(
    clone: Any,
    writer: str,
    session: str,
    record: str,
    *,
    remaining: Clock = lambda: events.DEFAULT_LOCK_TIMEOUT_S,
) -> list[dict]:
    return [
        line
        for line in _release_lines(_read(clone, remaining=remaining))
        if line.get("writer") == writer
        and line.get("session") == session
        and line.get("record") == record
        and isinstance(line.get("payload"), dict)
        and CANCELLED in line["payload"]
    ]


def _retire(
    ask: Any,
    selected: set[str],
    *,
    settled: bool = False,
    remaining: Clock = lambda: events.DEFAULT_LOCK_TIMEOUT_S,
) -> tuple[list[str], list[str]]:
    writer = queue.writer_of(ask.state)
    with events.LedgerLock(ask.clone.folder / "unsettled", timeout_s=remaining()):
        found = [
            line
            for line in _claims(ask.clone, remaining=remaining)
            if line["id"] in selected
            and line.get("writer") == writer
            and line["session"] == ask.session
            and line["record"] == ask.record
        ]
        held = claim.holder(ask.clone, ask.record)
        mine = (writer, ask.session)
        fenced = _fences(ask.clone, writer, ask.session, ask.record, remaining=remaining)
        markers = {line["payload"][CANCELLED] for line in fenced}
        wanted = [line["id"] for line in found]
        if settled and held == mine and not wanted and not fenced:
            wanted = ["settled:" + ask.record]
        made = []
        if held is None or held == mine:
            drafts = [
                {"kind": "unassign", "record": ask.record, "payload": {CANCELLED: identity}}
                for identity in wanted
                if identity not in markers
            ]
            if drafts:
                made = queue.write(
                    ask.clone,
                    drafts,
                    state=ask.state,
                    origin=queue.Origin(ask.session, False, ask.clock),
                    lock_wait=remaining(),
                )["events"]
        _drop(ask.clone, {line["id"] for line in found}, remaining=remaining)
        return [line["id"] for line in found], [line["id"] for line in fenced] + made


def _refusal(ask: Any, deadline: float) -> dict | None:
    if not isinstance(ask.session, str) or not ask.session or not ask.session.isprintable():
        return {
            "exit": claim.FIX,
            "fix": "name a printable session with --session or BASICLY_TRACKER_SESSION",
        }
    if (
        not isinstance(ask.record, str)
        or not ask.record
        or not ask.record.isprintable()
        or ask.record.startswith("-")
    ):
        return {
            "exit": claim.FIX,
            "fix": "name the record id to settle, as in claim acme-a1b2 --wait",
        }
    if type(deadline) not in (int, float) or not math.isfinite(deadline) or deadline <= 0:
        return {"exit": claim.FIX, "fix": "--deadline must be a positive finite number of seconds"}
    return None


def prepare(ask: Any, remaining: Clock) -> dict:
    writer = queue.writer_of(ask.state)
    with events.LedgerLock(ask.clone.folder / "unsettled", timeout_s=remaining()):
        if _fences(ask.clone, writer, ask.session, ask.record, remaining=remaining):
            return {
                "exit": claim.UNSETTLED,
                "record": ask.record,
                "unsettled": "this session's cancellation has not settled",
                "next": f"claim {ask.record} --cancel",
            }
        for line in _claims(ask.clone, remaining=remaining):
            if (line.get("writer"), line["session"], line["record"]) == (
                writer,
                ask.session,
                ask.record,
            ):
                return {"claim": line["id"]}
        made = queue.write(
            ask.clone,
            [{"kind": "claim", "record": ask.record, "payload": {"name": ask.name}}],
            state=ask.state,
            origin=queue.Origin(ask.session, False, ask.clock),
            lock_wait=remaining(),
        )["events"][0]
        return {"claim": made}


def _share(ask: Any, budget: Any, *, locked: bool) -> dict:
    plan = share.Plan(
        ask.clock, urgent=True, deadline_s=budget.left(), lock_wait=budget.left(), unlocked=locked
    )
    return share.share(ask.clone, ask.state, plan=plan, runner=ask.runner)


def _cancel(ask: Any, budget: Any) -> dict:
    with events.LedgerLock(ask.clone.folder / share.SHARE_LOCK, timeout_s=budget.left()):
        fetched = clone_api.fetch_with(
            ask.clone,
            clone_api.Fetch(
                clock=ask.clock,
                runner=ask.runner,
                timeout=budget.left(claim.PROBE),
                lock_wait=budget.left(),
                sharing=True,
                remaining=budget.left,
            ),
        )
        if not fetched.get("fetched"):
            return {
                "exit": claim.UNREACHABLE,
                "record": ask.record,
                "check": f"git fetch {ask.clone.url} refs/heads/main",
            }
        writer = queue.writer_of(ask.state)
        selected = {
            line["id"]
            for line in _claims(ask.clone, remaining=budget.left)
            if line.get("writer") == writer
            and line["session"] == ask.session
            and line["record"] == ask.record
        }
        held = claim.holder(ask.clone, ask.record)
        pending = _fences(ask.clone, writer, ask.session, ask.record, remaining=budget.left)
        if not selected and not pending and held != (writer, ask.session):
            return {
                "exit": claim.FIX,
                "record": ask.record,
                "fix": "this session has no unsettled or held claim to cancel",
            }
        removed, fences = _retire(ask, selected, settled=True, remaining=budget.left)
        if fences:
            _share(ask, budget, locked=True)
            if not set(fences) <= clone_api.valid_ids(ask.clone):
                return {
                    "exit": claim.UNSETTLED,
                    "record": ask.record,
                    "unsettled": "the cancellation has not reached the vault",
                    "next": f"claim {ask.record} --cancel",
                }
        return {
            "exit": claim.LANDED,
            "record": ask.record,
            "cancelled": removed,
            "unassign": fences,
        }


def cancel(ask: Any, *, ticks: Clock = time.monotonic, deadline: float = claim.DEADLINE) -> dict:
    if refused := _refusal(ask, deadline):
        return refused
    try:
        return _cancel(ask, claim._Budget(deadline, ticks))
    except _CANCEL_PENDING:
        return {
            "exit": claim.UNSETTLED,
            "record": ask.record,
            "unsettled": "cancellation did not settle before its deadline",
            "next": f"claim {ask.record} --cancel",
        }
    except _STATE_REFUSAL:
        return {
            "exit": claim.FIX,
            "record": ask.record,
            "fix": "preserve the pending queue and run fsck --repair before retrying cancellation",
        }


def wait(ask: Any, *, ticks: Clock = time.monotonic, deadline: float = claim.DEADLINE) -> dict:
    if refused := _refusal(ask, deadline):
        return refused
    writer = queue.writer_of(ask.state)
    wanted = [
        line
        for line in _claims(ask.clone)
        if line.get("writer") == writer
        and line["session"] == ask.session
        and line["record"] == ask.record
    ]
    started = bool(wanted)
    while True:
        budget = claim._Budget(deadline, ticks)
        try:
            fetched = clone_api.fetch_with(
                ask.clone,
                clone_api.Fetch(
                    clock=ask.clock,
                    runner=ask.runner,
                    timeout=budget.left(claim.PROBE),
                    lock_wait=budget.left(),
                    remaining=budget.left,
                ),
            )
            if fetched.get("fetched"):
                held = claim.holder(ask.clone, ask.record)
                if held is not None and held != (writer, ask.session):
                    _retire(ask, {line["id"] for line in wanted}, remaining=budget.left)
                    return claim._lost(ask.clone, ask.record) or claim._held_elsewhere(
                        ask.record, ask.clone.url, held
                    )
                cancelling = _fences(
                    ask.clone, writer, ask.session, ask.record, remaining=budget.left
                )
                if held == (writer, ask.session) and not cancelling:
                    return {"exit": claim.LANDED, "record": ask.record}
                current = [
                    line
                    for line in _claims(ask.clone, remaining=budget.left)
                    if line.get("writer") == writer
                    and line["session"] == ask.session
                    and line["record"] == ask.record
                ]
                if not current and not cancelling:
                    return {
                        "exit": claim.LOST if started else claim.FIX,
                        "record": ask.record,
                        "fix": "no pending claim remains; claim the record before waiting",
                    }
                _share(ask, budget, locked=False)
        except _WAIT_RETRY:
            print(f"claim {ask.record} remains unsettled; retrying", file=sys.stderr)
        except _INVALID_QUEUE:
            return {
                "exit": claim.FIX,
                "record": ask.record,
                "fix": "preserve the pending queue and run fsck --repair before waiting again",
            }
        time.sleep(claim.POLL)
