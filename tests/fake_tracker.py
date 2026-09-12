from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from basicly import dependency_graph, gate_source, label_source, tracker

if TYPE_CHECKING:
    import pytest

SHOW = "show"
COMMENTS_LIST = ("comments", "list")
GATE_LIST = ("gate", "list")


def _reply(fake: Callable[..., Any], repo_root: Path, args: list[str]) -> str:
    proc = fake(repo_root, args)
    return getattr(proc, "stdout", "") or ""


def _json(fake: Callable[..., Any], repo_root: Path, args: list[str]) -> Any:
    try:
        return json.loads(_reply(fake, repo_root, args))
    except ValueError:
        return None


def read_record(fake: Callable[..., Any], repo_root: Path, issue_id: str) -> dict | None:

    try:
        data = _json(fake, repo_root, [SHOW, issue_id, "--json"])
    except RuntimeError, OSError, ValueError:
        return None
    record = data[0] if isinstance(data, list) and data else data
    return record if isinstance(record, dict) else None


def _comments(fake: Callable[..., Any], repo_root: Path, issue_id: str) -> list[dict]:

    reply = _reply(fake, repo_root, [*COMMENTS_LIST, issue_id, "--json"])
    try:
        rows = json.loads(reply)
    except ValueError as exc:
        raise RuntimeError(f"comments list {issue_id} returned no usable JSON: {exc}") from exc
    return rows if isinstance(rows, list) else []


def _soft_comments(fake: Callable[..., Any], repo_root: Path, issue_id: str) -> list[dict]:
    try:
        return _comments(fake, repo_root, issue_id)
    except RuntimeError:
        return []


def _took(reply: object) -> bool:

    return int(getattr(reply, "returncode", 0) or 0) == 0


def _write(fake: Callable[..., Any], repo_root: Path, args: list[str]) -> None:

    if not _took(fake(repo_root, list(args))):
        raise RuntimeError(f"{' '.join(args)} did not reach the tracker")


def _soft_write(fake: Callable[..., Any], repo_root: Path, args: list[str]) -> bool:
    try:
        return _took(fake(repo_root, list(args)))
    except RuntimeError, OSError, ValueError:
        return False


def _add(fake: Callable[..., Any], repo_root: Path, issue_id: str, body: str) -> None:
    _write(fake, repo_root, ["comments", "add", issue_id, body])


def _soft_add(fake: Callable[..., Any], repo_root: Path, issue_id: str, body: str) -> bool:
    return _soft_write(fake, repo_root, ["comments", "add", issue_id, body])


def _all_texts(fake: Callable[..., Any], _repo_root: Path) -> dict[str, list[str]]:

    held = getattr(fake, "comments", None)
    owners = getattr(fake, "owners", {})
    if not held:
        return {}
    texts: dict[str, list[str]] = {}
    for index, text in enumerate(held):
        texts.setdefault(str(owners.get(index, "")), []).append(str(text))
    return {record: found for record, found in texts.items() if record}


def install(monkeypatch: pytest.MonkeyPatch, fake: Callable[..., Any]) -> None:

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

    record = tracker.read_record(repo_root, issue_id)
    if record is None:
        raise RuntimeError(f"br show {issue_id} returned no issue record")
    return record


def rebind(
    monkeypatch: pytest.MonkeyPatch, module: Any, name: str, replacement: Callable[..., Any]
) -> None:

    original = getattr(module, name)
    monkeypatch.setattr(module, name, replacement)
    for loaded in list(sys.modules.values()):
        if getattr(loaded, "__name__", "").split(".")[0] != "basicly":
            continue
        for attribute, value in list(vars(loaded).items()):
            if value is original:
                monkeypatch.setattr(loaded, attribute, replacement)


def _create(fake: Callable[..., Any], repo_root: Path, args: list[str]) -> str:

    reply = _json(fake, repo_root, list(args))
    minted = reply.get("id") if isinstance(reply, dict) else None
    if not isinstance(minted, str) or not minted:
        raise RuntimeError(f"{' '.join(args)} replied with no issue id")
    return minted


def _ranking(fake: Callable[..., Any], repo_root: Path, limit: int | None) -> dict:

    args = ["scheduler", "--json"]
    if limit is not None:
        args += ["--limit", str(limit)]
    payload = _json(fake, repo_root, args)
    if not isinstance(payload, dict):
        raise RuntimeError(f"the ranking read returned {type(payload).__name__}, not an object")
    return payload


def _cycles(fake: Callable[..., Any], repo_root: Path) -> tuple:

    report = _json(fake, repo_root, ["dep", "cycles", "--blocking-only", "--json"])
    rows = report.get("cycles") if isinstance(report, dict) else None
    found = []
    for cycle in rows if isinstance(rows, list) else ():
        members = cycle if isinstance(cycle, list) else cycle.get("issues", [])
        found.append(tuple(sorted(str(member) for member in members or ())))
    return tuple(found)


def _gates(fake: Callable[..., Any], repo_root: Path, issue_id: str) -> list[dict]:
    payload = _json(fake, repo_root, [*GATE_LIST, issue_id, "--robot"])
    results = payload.get("results") if isinstance(payload, dict) else None
    return [row for row in (results if isinstance(results, list) else []) if isinstance(row, dict)]


def _blocked(fake: Callable[..., Any], repo_root: Path) -> tuple:
    rows = _json(fake, repo_root, ["blocked", "--json"]) or ()
    return tuple(str(row["id"]) for row in rows if isinstance(row, dict) and "id" in row)


def _labelled(fake: Callable[..., Any], repo_root: Path, label: str) -> dict[str, str]:

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

    rebind(monkeypatch, dependency_graph, "blocked", lambda root: _blocked(fake, root))
    rebind(monkeypatch, label_source, "labelled", lambda root, label: _labelled(fake, root, label))
