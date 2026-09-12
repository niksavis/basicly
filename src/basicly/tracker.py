from __future__ import annotations

import contextlib
import contextvars
import json
import os
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from basicly import (
    comment_rows,
    owned_store,
    owned_write,
    redact,
    tracker_argv,
    tracker_usage,
)


class TrackerWriteRefusedError(Exception):
    pass


_read_only: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "br_read_only", default=None
)


@contextlib.contextmanager
def read_only(reason: str) -> Iterator[None]:

    token = _read_only.set(reason)
    try:
        yield
    finally:
        _read_only.reset(token)


def _refuse_write_in_read_only(args: Sequence[str]) -> None:

    if _read_only.get() is None:
        return
    surface, _ = tracker_usage.split_invocation(list(args))
    access = tracker_usage.classify_access(surface)
    if access == "read":
        return
    named = surface or " ".join(args)
    _refuse_in_read_only(
        f"{named} is not classified, and unknown is not read: classify it in "
        "tracker_usage if it only reads"
        if access == "unclassified"
        else f"{named} writes"
    )


def _refuse_in_read_only(fault: str) -> None:

    reason = _read_only.get()
    if reason is None:
        return
    raise TrackerWriteRefusedError(f"{reason} must write nothing, but {fault}")


MODE_OWNED = owned_store.MODE_OWNED
TRACKER_MODES = owned_store.TRACKER_MODES
DEFAULT_TRACKER_MODE = owned_store.DEFAULT_TRACKER_MODE
KIT_TRACKER_DIR = owned_store.KIT_TRACKER_DIR
LEDGER_DIR = owned_store.LEDGER_DIR
SCHEDULER_KIT_MODULE = owned_store.SCHEDULER_KIT_MODULE
TrackerDivergenceError = owned_store.TrackerDivergenceError
TrackerModeUnknownError = owned_store.TrackerModeUnknownError
set_mode_reader = owned_store.set_mode_reader
tracker_mode = owned_store.tracker_mode
ledger_dir = owned_store.ledger_dir
kit = owned_store.kit


def owned_record(repo_root: Path, issue_id: str) -> dict | None:

    try:
        kit_module = kit(repo_root)
        found = kit_module.read_ledger(ledger_dir(repo_root))
        ledger_fold = kit_module.events.fold(found)
        state = ledger_fold.records.get(issue_id)
        if state is None or state.tombstoned:
            return None
        views = kit_module.views_from_events(found)
    except TrackerDivergenceError, OSError, ValueError:
        return None
    return _rendered(kit_module, issue_id, state, views, ledger_fold.records)


def _rendered(
    kit_module: Any,
    issue_id: str,
    state: Any,
    views: Mapping[str, Any],
    states: Mapping[str, Any],
) -> dict:

    reserved = kit_module.migrate.RESERVED_KEYS
    record: dict = {key: value for key, value in state.fields.items() if key not in reserved}
    record["id"] = issue_id
    record["status"] = state.status or ""
    if tracker_argv.LABELS_FIELD in record:
        record[tracker_argv.LABELS_FIELD] = list(
            tracker_argv.labels_of(record[tracker_argv.LABELS_FIELD])
        )
    record["comments"] = [{"text": text} for text in state.comments]
    record.update(_edges(issue_id, views, states))
    return record


def _edges(issue_id: str, views: Mapping[str, Any], states: Mapping[str, Any]) -> dict[str, list]:

    view = views.get(issue_id)
    return {
        "dependencies": [
            {
                "id": edge.target,
                "dependency_type": edge.type,
                "status": _edge_status(views, edge.target),
            }
            for edge in (view.dependencies if view is not None else ())
        ],
        "dependents": [
            {
                "id": other,
                "dependency_type": edge.type,
                "status": held.status or "",
                "title": str(states[other].fields.get("title", "")) if other in states else "",
            }
            for other, held in sorted(views.items())
            for edge in held.dependencies
            if edge.target == issue_id and not held.tombstoned
        ],
    }


def _edge_status(views: Mapping[str, Any], issue_id: str) -> str:
    view = views.get(issue_id)
    return "unknown" if view is None else view.status or ""


def record_edges(repo_root: Path, issue_id: str) -> dict[str, list]:

    try:
        kit_module = kit(repo_root)
        found = kit_module.read_ledger(ledger_dir(repo_root))
        states = kit_module.events.fold(found).records
        views = kit_module.views_from_events(found)
    except TrackerDivergenceError, OSError, ValueError:
        return {"dependencies": [], "dependents": []}
    return _edges(issue_id, views, states)


COMMENT_TEXT_KEY = comment_rows.TEXT_KEY


_LOCK_CONTENTION = ("another writer holds", "went stale")


def is_transient_storage_error(text: str) -> bool:

    return any(marker in text for marker in _LOCK_CONTENTION)


def _comments_add_argv(issue_id: str, body: str) -> list[str]:

    return ["comments", "add", issue_id, body]


_FIELD_FLAGS = {
    name: max(
        (flag for flag, held in tracker_argv.UPDATE_FIELD_FLAGS.items() if held == name), key=len
    )
    for name in set(tracker_argv.UPDATE_FIELD_FLAGS.values())
}


@dataclass(frozen=True)
class WriteReceipt:
    landed: tuple[str, ...]
    replayed: int

    def __bool__(self) -> bool:
        return self.replayed == 0


def _fact(kit_module: Any, event: Any) -> str:

    payload = dict(event.payload)
    kinds = kit_module.events
    if event.kind == kinds.KIND_FIELD:
        name = str(payload.get("name", ""))
        return f"{event.record} {_FIELD_FLAGS.get(name, name)}={payload.get('value')}"
    if event.kind == kinds.KIND_STATUS:
        return f"{event.record} --status={payload.get('status')}"
    return f"{event.record} {event.kind}"


def _refuse_a_write_the_store_did_not_keep(repo_root: Path, landed: list[Any]) -> None:

    kit_module = kit(repo_root)
    logs = kit_module.events.log_paths(ledger_dir(repo_root))
    text = "".join(path.read_text(encoding="utf-8") for path in logs)
    lost = [event for event in landed if f'"id":"{event.id}"' not in text]
    if not lost:
        return
    raise TrackerDivergenceError(
        f"the ledger does not hold what it just accepted: "
        f"{', '.join(_fact(kit_module, event) for event in lost)} — the append returned "
        f"{len(landed)} event(s) and a re-read of the log finds {len(landed) - len(lost)}, "
        f"so another process is rewriting it and nothing here is recorded"
    )


def _appended(repo_root: Path, args: list[str]) -> WriteReceipt:
    stamped, landed = owned_write.append(repo_root, args)
    _refuse_a_write_the_store_did_not_keep(repo_root, landed)
    kit_module = kit(repo_root)
    return WriteReceipt(
        landed=tuple(_fact(kit_module, event) for event in landed),
        replayed=len(stamped) - len(landed),
    )


def write(repo_root: Path, args: list[str]) -> WriteReceipt:

    _refuse_write_in_read_only(args)
    if args and args[0] == "create":
        raise RuntimeError(f"a create names an id the store mints; call create_record: {args}")
    return _appended(repo_root, args)


def create_record(repo_root: Path, args: list[str]) -> str:

    _refuse_write_in_read_only(args)
    return owned_write.create(repo_root, args)


def try_write(repo_root: Path, args: list[str]) -> bool:

    _refuse_write_in_read_only(args)
    try:
        _appended(repo_root, args)
    except TrackerDivergenceError:
        return False
    return True


def _owned_comment_rows(repo_root: Path) -> dict[str, list[dict]]:

    kit_module = kit(repo_root)
    return comment_rows.from_ledger(kit_module, kit_module.read_ledger(ledger_dir(repo_root)))


def add_comment(repo_root: Path, issue_id: str, body: str) -> None:

    write(repo_root, _comments_add_argv(issue_id, body))


def try_add_comment(repo_root: Path, issue_id: str, body: str) -> bool:
    return try_write(repo_root, _comments_add_argv(issue_id, body))


def read_comments(repo_root: Path, issue_id: str) -> list[dict]:

    return _owned_comment_rows(repo_root).get(issue_id, [])


def try_read_comments(repo_root: Path, issue_id: str) -> list[dict]:

    try:
        return _owned_comment_rows(repo_root).get(issue_id, [])
    except TrackerDivergenceError, OSError, ValueError:
        return []


def all_comment_texts(repo_root: Path) -> dict[str, list[str]]:

    rows = all_comment_rows(repo_root)
    return {record: [str(row[COMMENT_TEXT_KEY]) for row in found] for record, found in rows.items()}


def all_comment_rows(repo_root: Path) -> dict[str, list[dict]]:

    try:
        return _owned_comment_rows(repo_root)
    except TrackerDivergenceError, OSError, ValueError:
        return {}


ARTIFACT_KIND_KEY = "artifact"
ARTIFACT_BODY_KEY = "body"


def add_artifact(repo_root: Path, issue_id: str, kind: str, body: object) -> None:

    _refuse_in_read_only(f"recording {issue_id}'s {kind} artifact writes")
    kit_module = kit(repo_root)
    events = kit_module.events
    draft = events.Draft(
        issue_id,
        events.KIND_ARTIFACT,
        {
            kit_module.migrate.PROVENANCE_KEY: owned_write.OWNED_PROVENANCE,
            ARTIFACT_KIND_KEY: kind,
            ARTIFACT_BODY_KEY: body,
        },
    )
    ledger = ledger_dir(repo_root)
    try:
        with events.LedgerLock(ledger) as lock:
            owned_write.refuse_a_write_to_an_absent_record(
                kit_module, ledger, f"the {kind} artifact for {issue_id}", [draft]
            )
            events.append(ledger, [draft], redact=redact.redact_committed, held_lock=lock)
    except (events.LedgerError, OSError, ValueError) as exc:
        raise TrackerDivergenceError(
            f"the {kind} artifact for {issue_id} did not reach the owned ledger: {exc}"
        ) from exc


def read_artifacts(repo_root: Path, issue_id: str) -> dict[str, object]:

    kit_module = kit(repo_root)
    ledger_fold = kit_module.events.fold(kit_module.read_ledger(ledger_dir(repo_root)))
    state = ledger_fold.records.get(issue_id)
    if state is None or state.tombstoned:
        return {}
    return dict(state.artifacts)


DISPATCH_SPEND_KEY = "spend_micros"


def add_dispatch(repo_root: Path, issue_id: str, reading: Mapping[str, object]) -> None:

    _refuse_in_read_only(f"recording {issue_id}'s dispatch spend writes")
    kit_module = kit(repo_root)
    events = kit_module.events
    draft = events.Draft(
        issue_id,
        events.KIND_DISPATCH,
        {kit_module.migrate.PROVENANCE_KEY: owned_write.OWNED_PROVENANCE, **reading},
    )
    ledger = ledger_dir(repo_root)
    try:
        with events.LedgerLock(ledger) as lock:
            owned_write.refuse_a_write_to_an_absent_record(
                kit_module, ledger, f"the dispatch spend for {issue_id}", [draft]
            )
            events.append(ledger, [draft], redact=redact.redact_committed, held_lock=lock)
    except (events.LedgerError, OSError, ValueError) as exc:
        raise TrackerDivergenceError(
            f"the dispatch spend for {issue_id} did not reach the owned ledger: {exc}"
        ) from exc


_PUBLISH_DEADLINE_S = 5.0
_PUBLISH_FIRST_WAIT_S = 0.005
_PUBLISH_MAX_WAIT_S = 0.1
_READ_DEADLINE_S = 1.0


def _publish(tmp: Path, export: Path) -> bool:

    deadline = time.monotonic() + _PUBLISH_DEADLINE_S
    delay = _PUBLISH_FIRST_WAIT_S
    while True:
        try:
            tmp.replace(export)
        except OSError:
            if time.monotonic() >= deadline:
                tmp.unlink(missing_ok=True)
                return False
            time.sleep(delay)
            delay = min(delay * 2, _PUBLISH_MAX_WAIT_S)
        else:
            return True


def _dump_record(record: dict[str, object]) -> str:

    return json.dumps(record, separators=(",", ":"), ensure_ascii=False)


def dependency_edge(dep: object) -> tuple[str, str] | None:

    if not isinstance(dep, dict):
        return None
    dep_id = dep.get("depends_on_id") or dep.get("id")
    dep_type = dep.get("dependency_type") or dep.get("type")
    if not isinstance(dep_id, str) or not dep_id:
        return None
    return dep_id, dep_type if isinstance(dep_type, str) else ""


def read_record(repo_root: Path, issue_id: str) -> dict | None:

    return owned_record(repo_root, issue_id)


def require_record(repo_root: Path, issue_id: str) -> dict:

    record = read_record(repo_root, issue_id)
    if record is None:
        raise RuntimeError(f"the tracker holds no usable record for {issue_id}")
    return record


def owned_ranking(repo_root: Path, limit: int | None = None) -> dict:

    scheduler = kit(repo_root, SCHEDULER_KIT_MODULE)
    answer = scheduler.ranking(ledger_dir(repo_root), limit=limit)
    return {
        "schema": answer.schema,
        "fallback_policy": {"sort": answer.sort},
        "recommendations": [
            {
                "rank": entry.rank,
                "fallback_rank": entry.rank,
                "score": entry.score,
                "issue": {"id": entry.record, "title": entry.title},
            }
            for entry in answer.records
        ],
    }


def read_ranking(repo_root: Path, limit: int | None = None) -> dict:

    return owned_ranking(repo_root, limit)


def _redact_paths(value: object) -> object:

    if isinstance(value, str):
        return redact.redact_committed(value)
    if isinstance(value, dict):
        return {key: _redact_paths(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_paths(item) for item in value]
    return value


def _event_generation(counts: dict[tuple[str, str, str], int], event: Mapping[str, object]) -> int:
    key = (
        str(event.get("record", "")),
        str(event.get("kind", "")),
        json.dumps(event.get("payload") or {}, sort_keys=True, separators=(",", ":")),
    )
    counts[key] = counts.get(key, 0) + 1
    return counts[key]


def _refuse_a_rename_past_the_hold(events: Any, tmp: Path, held_s: float) -> None:

    if held_s <= events.LOCK_STALE_AFTER_S:
        return
    tmp.unlink(missing_ok=True)
    raise TrackerDivergenceError(
        f"{tmp.name}: the rewrite held the ledger lock {held_s:.1f}s, past the"
        f" {events.LOCK_STALE_AFTER_S}s a waiter may steal it at, so nothing is renamed"
    )


def scrub_ledger(
    repo_root: Path,
    *,
    lock_timeout_s: float | None = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> int:

    files = sorted(ledger_dir(repo_root).glob("events-*.jsonl"))
    if not files:
        return 0
    kit_module = kit(repo_root)
    events = kit_module.events
    timeout_s = events.DEFAULT_LOCK_TIMEOUT_S if lock_timeout_s is None else lock_timeout_s
    changed = 0
    with events.LedgerLock(ledger_dir(repo_root), timeout_s=timeout_s):
        held_since = monotonic()
        for path in files:
            raw = path.read_text(encoding="utf-8")
            stored: dict[tuple[str, str, str], int] = {}
            minted: dict[tuple[str, str, str], int] = {}
            lines: list[str] = []
            for line in raw.splitlines():
                if not line.strip():
                    lines.append(line)
                    continue
                event = json.loads(line)
                generation = _event_generation(stored, event)
                expected = events.event_id_for(
                    event["record"], event["kind"], event["payload"], generation=generation
                )
                if expected != event["id"]:
                    raise TrackerDivergenceError(
                        f"{path.name}: {event['id']} does not re-mint from its own content, so"
                        " the generation cannot be derived and nothing is rewritten"
                    )
                scrubbed = dict(event)
                scrubbed["actor"] = redact.redact_committed(str(event.get("actor") or ""))
                scrubbed["payload"] = _redact_paths(event["payload"])
                scrubbed["id"] = events.event_id_for(
                    scrubbed["record"],
                    scrubbed["kind"],
                    scrubbed["payload"],
                    generation=_event_generation(minted, scrubbed),
                )
                if scrubbed == event:
                    lines.append(line)
                    continue
                lines.append(_dump_record(scrubbed))
                changed += 1
            if not changed:
                continue
            trailer = "\n" if raw.endswith("\n") else ""
            tmp = path.with_suffix(f".{os.getpid()}.jsonl.tmp")
            tmp.write_text("\n".join(lines) + trailer, encoding="utf-8")
            _refuse_a_rename_past_the_hold(events, tmp, monotonic() - held_since)
            _publish(tmp, path)
    return changed


def all_views(repo_root: Path) -> dict[str, Any]:

    kit_module = kit(repo_root)
    views = kit_module.views_from_events(kit_module.read_ledger(ledger_dir(repo_root)))
    return {record: view for record, view in views.items() if not view.tombstoned}


def all_records(repo_root: Path) -> list[dict]:

    try:
        kit_module = kit(repo_root)
        found = kit_module.read_ledger(ledger_dir(repo_root))
        ledger_fold = kit_module.events.fold(found)
        views = kit_module.views_from_events(found)
    except TrackerDivergenceError, OSError, ValueError:
        return []
    return [
        _rendered(kit_module, issue_id, ledger_fold.records[issue_id], views, ledger_fold.records)
        for issue_id in sorted(ledger_fold.records)
        if not ledger_fold.records[issue_id].tombstoned
    ]


def export_comment_texts(record: Mapping[str, object]) -> list[str]:
    comments = record.get("comments")
    if not isinstance(comments, list):
        return []
    return [
        str(comment["text"])
        for comment in comments
        if isinstance(comment, Mapping) and isinstance(comment.get("text"), str)
    ]
