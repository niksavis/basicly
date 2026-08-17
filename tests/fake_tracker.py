"""Route the engine's tracker seams at an argv-speaking stand-in (basicly-vkh0.42.7).

**What this replaces, and why it is not a spawn.** Two dozen fixtures used to patch
``tracker.run_br`` — one function every tracker call passed through on its way to a
subprocess. There is no subprocess any more, so there is no single funnel: the engine
reads a record, its comments, its gates, the blocking graph and the ranking through five
seams, and writes through two.

The stand-ins those fixtures carry are worth keeping. Each is a small state machine
routed by subcommand, and each encodes what a test needs the tracker to say — so this
module adapts *the seams* onto the argv shape they already speak, instead of asking
twenty-four fixtures to be rewritten into ledger seeds.

**A fixture that needs the real store uses ``flipped_tracker`` instead.** That is the
stronger instrument and the one the flip's own criterion is asserted with: it seeds a
real ledger and fails the test on any spawn. This module is for a test about *engine
behaviour given what the tracker says*, where authoring a ledger would be describing the
fixture rather than the behaviour.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from basicly import dependency_graph, gate_source, label_source, tracker

if TYPE_CHECKING:
    import pytest

# What a stand-in is handed for a read it is expected to answer. Each mirrors the argv
# the engine used to spawn, so a fixture written against the old funnel is unchanged.
SHOW = "show"
COMMENTS_LIST = ("comments", "list")
GATE_LIST = ("gate", "list")


def _reply(fake: Callable[..., Any], repo_root: Path, args: list[str]) -> str:
    """One stand-in call, as the stdout it would have produced."""
    proc = fake(repo_root, args)
    return getattr(proc, "stdout", "") or ""


def _json(fake: Callable[..., Any], repo_root: Path, args: list[str]) -> Any:
    """One stand-in call, parsed; ``None`` when it answered nothing usable."""
    try:
        return json.loads(_reply(fake, repo_root, args))
    except ValueError:
        return None


def read_record(fake: Callable[..., Any], repo_root: Path, issue_id: str) -> dict | None:
    """*issue_id* as the stand-in holds it, unwrapped from either spelling.

    A single record arrives as a bare object or as a one-element array, which is the
    unwrap :func:`basicly.tracker.read_record` has always owned on the caller's behalf.

    **None for every way the read comes back without a record, never an exception** —
    that is the seam's own contract, and a caller that has to tell an absent record from
    an unreadable store reads the None rather than catching. A stand-in that raises is
    modelling a store that cannot answer, which is one of those ways.
    """
    try:
        data = _json(fake, repo_root, [SHOW, issue_id, "--json"])
    except RuntimeError, OSError, ValueError:
        return None
    record = data[0] if isinstance(data, list) and data else data
    return record if isinstance(record, dict) else None


def _comments(fake: Callable[..., Any], repo_root: Path, issue_id: str) -> list[dict]:
    """*issue_id*'s comment rows, oldest first — the hard read, which raises.

    A store that cannot answer must raise rather than report an empty history: every
    family read through this seam is a counter or a refusal, so "no markers recorded"
    read out of a broken store spends a rework budget twice.
    """
    reply = _reply(fake, repo_root, [*COMMENTS_LIST, issue_id, "--json"])
    try:
        rows = json.loads(reply)
    except ValueError as exc:
        raise RuntimeError(f"comments list {issue_id} returned no usable JSON: {exc}") from exc
    return rows if isinstance(rows, list) else []


def _soft_comments(fake: Callable[..., Any], repo_root: Path, issue_id: str) -> list[dict]:
    """The soft half: an unanswerable store reads as no markers, which its callers act on."""
    try:
        return _comments(fake, repo_root, issue_id)
    except RuntimeError:
        return []


def _took(reply: object) -> bool:
    """Whether the stand-in took the write.

    A stand-in that answers a process-shaped object states the outcome in its return
    code, which is how a fixture models a store that refused. Anything else — ``None``,
    a bare string — is a stand-in that only records the call, and that is a write taken.
    """
    return int(getattr(reply, "returncode", 0) or 0) == 0


def _write(fake: Callable[..., Any], repo_root: Path, args: list[str]) -> None:
    """The hard half: a refused write raises, so no caller claims a record it has not.

    Raises:
        RuntimeError: the stand-in refused, which is what the real seam reports.
    """
    if not _took(fake(repo_root, list(args))):
        raise RuntimeError(f"{' '.join(args)} did not reach the tracker")


def _soft_write(fake: Callable[..., Any], repo_root: Path, args: list[str]) -> bool:
    """The soft half: False when the write did not land, for a caller that tolerates it."""
    try:
        return _took(fake(repo_root, list(args)))
    except RuntimeError, OSError, ValueError:
        return False


def _add(fake: Callable[..., Any], repo_root: Path, issue_id: str, body: str) -> None:
    _write(fake, repo_root, ["comments", "add", issue_id, body])


def _soft_add(fake: Callable[..., Any], repo_root: Path, issue_id: str, body: str) -> bool:
    return _soft_write(fake, repo_root, ["comments", "add", issue_id, body])


def _all_texts(fake: Callable[..., Any], _repo_root: Path) -> dict[str, list[str]]:
    """Every record's comment bodies. Served off the stand-in's own list when it keeps one.

    The whole-tracker read has no argv a per-record stand-in answers, so it is taken from
    the object rather than asked for — a stand-in with no comment list answers nothing,
    which is what a test that never seeds one expects.
    """
    held = getattr(fake, "comments", None)
    owners = getattr(fake, "owners", {})
    if not held:
        return {}
    texts: dict[str, list[str]] = {}
    for index, text in enumerate(held):
        texts.setdefault(str(owners.get(index, "")), []).append(str(text))
    return {record: found for record, found in texts.items() if record}


def install(monkeypatch: pytest.MonkeyPatch, fake: Callable[..., Any]) -> None:
    """Point every tracker seam at *fake*, which answers in the argv shape.

    **Every alias, not only the definition.** The engine binds these seams at import
    (``from .tracker import read_comments as _read_comments``), so patching the defining
    module alone leaves each importer holding the original — which is exactly what the
    old ``run_br`` patch got for free by sitting one layer below them all. Every loaded
    ``basicly`` module is walked and any attribute still bound to the original is
    rebound, so a new importer needs no change here.

    A seam the stand-in does not know about raises out of the stand-in itself, which is
    the direction that surfaces a new dependency rather than hiding it.
    """
    replacements: list[tuple[Any, str, Callable[..., Any]]] = [
        (tracker, "read_record", lambda root, rid: read_record(fake, root, rid)),
        (tracker, "require_record", _require),
        (tracker, "read_comments", lambda root, rid: _comments(fake, root, rid)),
        (tracker, "try_read_comments", lambda root, rid: _soft_comments(fake, root, rid)),
        (tracker, "all_comment_texts", lambda root: _all_texts(fake, root)),
        (tracker, "write", lambda root, args: _write(fake, root, args)),
        (tracker, "try_write", lambda root, args: _soft_write(fake, root, args)),
        (tracker, "add_comment", lambda root, rid, body: _add(fake, root, rid, body)),
        (tracker, "try_add_comment", lambda root, rid, body: _soft_add(fake, root, rid, body)),
        (tracker, "create_record", lambda root, args: _create(fake, root, args)),
        (tracker, "read_ranking", lambda root, limit=None: _ranking(fake, root, limit)),
        (gate_source, "read_gates", lambda root, rid: _gates(fake, root, rid)),
        (dependency_graph, "blocking_cycles", lambda root: _cycles(fake, root)),
    ]
    for module, name, replacement in replacements:
        rebind(monkeypatch, module, name, replacement)


def _require(repo_root: Path, issue_id: str) -> dict:
    """The hard half of the record read, keeping the one message every absence raises.

    Composed from ``tracker.read_record`` rather than from the stand-in directly, exactly as
    the real module does: a test that stubs only the soft read must still see the hard
    one refuse, and a stand-in reached behind that seam would answer past the stub.
    """
    record = tracker.read_record(repo_root, issue_id)
    if record is None:
        raise RuntimeError(f"br show {issue_id} returned no issue record")
    return record


def rebind(
    monkeypatch: pytest.MonkeyPatch, module: Any, name: str, replacement: Callable[..., Any]
) -> None:
    """Replace ``module.name`` and every ``basicly`` alias still bound to the original.

    Public for a test that stubs one seam rather than the whole tracker: patching the
    defining module alone leaves each importer holding the original, because the engine
    binds these at import (``from .tracker import read_record as _read_record``).
    """
    original = getattr(module, name)
    monkeypatch.setattr(module, name, replacement)
    for loaded in list(sys.modules.values()):
        if getattr(loaded, "__name__", "").split(".")[0] != "basicly":
            continue
        for attribute, value in list(vars(loaded).items()):
            if value is original:
                monkeypatch.setattr(loaded, attribute, replacement)


def _create(fake: Callable[..., Any], repo_root: Path, args: list[str]) -> str:
    """The one write whose result the caller needs: the id the store minted.

    Raises:
        RuntimeError: the stand-in's reply carried no id, which is the same failure the
            seam reports when nothing could be minted.
    """
    reply = _json(fake, repo_root, list(args))
    minted = reply.get("id") if isinstance(reply, dict) else None
    if not isinstance(minted, str) or not minted:
        raise RuntimeError(f"{' '.join(args)} replied with no issue id")
    return minted


def _ranking(fake: Callable[..., Any], repo_root: Path, limit: int | None) -> dict:
    """The ranked ready set, as the payload `loop_state` parses.

    Raises:
        RuntimeError: the stand-in answered nothing usable. An unrankable ready set is a
            stop, never an empty list — an empty list reads as "nothing to do" and the
            loop would idle instead of reporting.
    """
    args = ["scheduler", "--json"]
    if limit is not None:
        args += ["--limit", str(limit)]
    payload = _json(fake, repo_root, args)
    if not isinstance(payload, dict):
        raise RuntimeError(f"the ranking read returned {type(payload).__name__}, not an object")
    return payload


def _cycles(fake: Callable[..., Any], repo_root: Path) -> tuple:
    """Every blocking cycle's members, in the shape `dependency_graph` answers.

    Both spellings are read — a bare list of ids, or an object carrying ``issues`` —
    because that is what the seam has always accepted from either side.
    """
    report = _json(fake, repo_root, ["dep", "cycles", "--blocking-only", "--json"])
    rows = report.get("cycles") if isinstance(report, dict) else None
    found = []
    for cycle in rows if isinstance(rows, list) else ():
        members = cycle if isinstance(cycle, list) else cycle.get("issues", [])
        found.append(tuple(sorted(str(member) for member in members or ())))
    return tuple(found)


def _gates(fake: Callable[..., Any], repo_root: Path, issue_id: str) -> list[dict]:
    """*issue_id*'s gate rows, in the ``results`` shape every reader parses."""
    payload = _json(fake, repo_root, [*GATE_LIST, issue_id, "--robot"])
    results = payload.get("results") if isinstance(payload, dict) else None
    return [row for row in (results if isinstance(results, list) else []) if isinstance(row, dict)]


def _blocked(fake: Callable[..., Any], repo_root: Path) -> tuple:
    """The ids waiting on a dependency, in the shape `dependency_graph` answers."""
    rows = _json(fake, repo_root, ["blocked", "--json"]) or ()
    return tuple(str(row["id"]) for row in rows if isinstance(row, dict) and "id" in row)


def _labelled(fake: Callable[..., Any], repo_root: Path, label: str) -> dict[str, str]:
    """``{record: status}`` for *label*, whichever payload shape the stand-in uses.

    Both spellings are read — a bare list, or an object carrying ``issues`` — because
    that is what the seam has always accepted. **Closed records are in**: a selection
    whose every record has closed is a finished pass, and an empty one reports it blocked.
    """
    payload = _json(fake, repo_root, ["list", "--label", label, "--json"])
    rows = payload.get("issues") if isinstance(payload, dict) else payload
    found = {
        str(row["id"]): str(row.get("status", ""))
        for row in rows or ()
        if isinstance(row, dict) and "id" in row
    }
    closed = _json(fake, repo_root, ["list", "--label", label, "--status", "closed", "--json"])
    closed_rows = closed.get("issues") if isinstance(closed, dict) else closed
    found.update({
        str(row["id"]): str(row.get("status", ""))
        for row in closed_rows or ()
        if isinstance(row, dict) and "id" in row
    })
    return found


def install_graph(monkeypatch: pytest.MonkeyPatch, fake: Callable[..., Any]) -> None:
    """Additionally point the blocking graph and the label query at *fake*.

    Separate from :func:`install` because most fixtures never reach either, and a stand-in
    that has to answer a query its test does not exercise is a fixture describing itself.
    """
    rebind(monkeypatch, dependency_graph, "blocked", lambda root: _blocked(fake, root))
    rebind(monkeypatch, label_source, "labelled", lambda root, label: _labelled(fake, root, label))
