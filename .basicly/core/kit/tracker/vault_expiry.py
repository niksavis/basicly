from __future__ import annotations

import importlib.util
import math
import sys
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
        raise ImportError("Claim expiry support is missing; reinstall the tracker kit.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[key] = module
    spec.loader.exec_module(module)
    return module


settlement = _load("vault_unsettled")
clone_api = settlement.clone_api
claim = settlement.claim
Clock = Callable[[], float]


def due(clone: Any, clock: Clock, remaining: Clock) -> list[dict]:
    pending = settlement.queue.pending(clone.folder / settlement.queue.QUEUE_NAME)
    if not any(line.get("kind") == "claim" for line in pending):
        return []
    now = clock()
    if not math.isfinite(now):
        raise clone_api.CloneError("The tracker clock is invalid; preserve the pending claims.")
    return [
        line
        for line in settlement._claims(clone, remaining=remaining)
        if now - line["at"] >= settlement.EXPIRY
    ]


def _retire(clone: Any, clock: Clock, runner: Any, remaining: Clock) -> list[str]:
    state = clone.folder.parent.parent
    writer = settlement.queue.writer_of(state)
    groups: dict[tuple[str, str], set[str]] = {}
    for line in due(clone, clock, remaining):
        if line.get("writer") == writer:
            groups.setdefault((line["record"], line["session"]), set()).add(line["id"])
    expired = []
    for (record, session), identities in sorted(groups.items()):
        ask = claim.Ask(clone, state, record, session, clock, runner=runner)
        retired, _fences = settlement._retire(ask, identities, remaining=remaining)
        expired.extend(retired)
    return expired


def after_fetch(
    clone: Any,
    clock: Clock,
    runner: Any,
    remaining: Clock,
    *,
    sharing: bool,
) -> list[str]:
    settlement.validate_pending(clone, remaining)
    if not due(clone, clock, remaining):
        return []
    if sharing:
        return _retire(clone, clock, runner, remaining)
    state = clone.folder.parent.parent
    with settlement.events.LedgerLock(
        clone.folder / settlement.share.SHARE_LOCK, timeout_s=remaining()
    ):
        fetched = clone_api.fetch_with(
            clone,
            clone_api.Fetch(
                clock=clock,
                runner=runner,
                timeout=min(claim.PROBE, remaining()),
                lock_wait=remaining(),
                sharing=True,
                remaining=remaining,
            ),
        )
        if not fetched.get("fetched"):
            raise clone_api.CloneError(
                "Unsettled claims could not be reconciled; retry fetch before working."
            )
        expired = fetched.get("expired", [])
        if expired:
            plan = settlement.share.Plan(
                clock, urgent=True, deadline_s=remaining(), lock_wait=remaining(), unlocked=True
            )
            settlement.share.share(clone, state, plan=plan, runner=runner)
        return expired


def before_share(clone: Any, clock: Clock, runner: Any, remaining: Clock) -> bool:
    settlement.validate_pending(clone, remaining)
    if not due(clone, clock, remaining):
        return False
    fetched = clone_api.fetch_with(
        clone,
        clone_api.Fetch(
            clock=clock,
            runner=runner,
            timeout=min(claim.PROBE, remaining()),
            lock_wait=remaining(),
            sharing=True,
            remaining=remaining,
        ),
    )
    if not fetched.get("fetched"):
        raise clone_api.CloneError("Expired claims remain queued; retry fetch before sharing them.")
    return bool(fetched.get("expired"))
