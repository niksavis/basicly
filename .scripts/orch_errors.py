from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from orch_error_classes import Result, Use, classify, command_of, shown_input

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


@dataclass
class Incident:
    result: Result
    use: Use | None
    kind: str
    context: list[str] = field(default_factory=list)


def slug(path: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", path)


def project_of(folder: str, workspace_slug: str, workspace_name: str) -> str:
    rest = folder[len(workspace_slug) :].lstrip("-")
    head = re.split(r"-worktrees-|--", rest)[0]
    return head or workspace_name


def transcripts(root: Path, workspace_slug: str) -> list[tuple[Path, Path]]:
    found = []
    for folder in sorted(root.glob(workspace_slug + "*")):
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.jsonl")):
            found.append((path, path))
            found.extend(
                (sub, path) for sub in sorted((folder / path.stem).glob("subagents/*.jsonl"))
            )
    return found


def _content_text(content: Any) -> str:
    if isinstance(content, list):
        return "\n".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
    return "" if content is None else str(content)


def _rows(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def session_name(rows: list[dict[str, Any]]) -> str | None:
    names = [
        row.get("agentName")
        for row in rows
        if row.get("type") == "agent-name" and row.get("agentName")
    ]
    return str(names[-1]) if names else None


def collect(root: Path, workspace: str) -> tuple[dict[str, Use], dict[str, Result]]:
    workspace_slug = slug(workspace)
    workspace_name = Path(workspace).name
    uses: dict[str, Use] = {}
    results: dict[str, Result] = {}
    names: dict[Path, str] = {}
    for path, owner in transcripts(root, workspace_slug):
        rows = _rows(path)
        project = project_of(
            path.parent.name if path == owner else owner.parent.name, workspace_slug, workspace_name
        )
        if path == owner:
            names[owner] = session_name(rows) or project
        session = names.get(owner, project)
        for row in rows:
            message = row.get("message")
            if not isinstance(message, dict) or not isinstance(message.get("content"), list):
                continue
            for block in message["content"]:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use" and isinstance(block.get("id"), str):
                    uses.setdefault(
                        block["id"],
                        Use(str(block.get("name")), block.get("input"), row.get("parentUuid")),
                    )
                elif block.get("type") == "tool_result" and isinstance(
                    block.get("tool_use_id"), str
                ):
                    _keep(results, block, row, session, project)
    return uses, results


def _keep(
    results: dict[str, Result],
    block: dict[str, Any],
    row: dict[str, Any],
    session: str,
    project: str,
) -> None:
    key = block["tool_use_id"]
    stamp = row.get("timestamp") if isinstance(row.get("timestamp"), str) else None
    held = results.get(key)
    if held is not None and (held.stamp is not None or stamp is None):
        return
    decision = row.get("permissionDecision")
    results[key] = Result(
        key,
        stamp,
        bool(block.get("is_error")),
        _ANSI.sub("", _content_text(block.get("content"))),
        decision if isinstance(decision, dict) else {},
        row.get("uuid"),
        session,
        project,
    )


def incidents(
    uses: dict[str, Use], results: dict[str, Result], since: str, until: str = "9999"
) -> tuple[list[Incident], int, int]:
    by_uuid = {result.uuid: result for result in results.values() if result.uuid}
    beside = {
        (result.session, result.stamp): result
        for result in results.values()
        if result.error and result.stamp and not result.tool_use_id.startswith("repl-error-")
    }
    chosen: dict[str, Incident] = {}
    script_throws = []
    undated = 0
    for key, result in results.items():
        if not result.error:
            continue
        if result.stamp is None:
            undated += 1
            continue
        if not since <= result.stamp < until:
            continue
        use = uses.get(key)
        parent = None
        if key.startswith("repl-error-"):
            parent = by_uuid.get(use.parent) if use and use.parent else None
            if parent is None or not parent.error:
                parent = beside.get((result.session, result.stamp))
        if parent is not None and parent.error:
            script_throws.append((parent.tool_use_id, result.text))
            continue
        chosen[key] = Incident(result, use, classify(result, use))
    for target, text in script_throws:
        if target in chosen:
            chosen[target].context.append(text.splitlines()[0][:120] if text else "")
    ordered = sorted(chosen.values(), key=lambda item: item.result.stamp or "")
    return ordered, undated, len(script_throws)


def load_state(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {"classes": [], "until": None}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("classes"), list):
        raise ValueError(
            f"state file {path} is not an orch errors state; remove it or pass another --state"
        )
    return data


def save_state(path: Path, classes: set[str], until: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=".orch-errors-")
    with os.fdopen(handle, "w", encoding="utf-8") as out:
        json.dump({"classes": sorted(classes), "until": until}, out, indent=1)
    Path(temporary).replace(path)


def report(
    found: list[Incident], totals: Counter[str], seen: set[str], until: str | None
) -> dict[str, Any]:
    projects: dict[str, Any] = {}
    grouped: dict[str, dict[str, list[Incident]]] = defaultdict(lambda: defaultdict(list))
    for item in found:
        grouped[item.result.project][item.kind].append(item)
    for project in sorted(set(totals) | set(grouped)):
        classes = grouped.get(project, {})
        sessions = Counter(item.result.session for items in classes.values() for item in items)
        rows = []
        for kind, items in sorted(classes.items(), key=lambda pair: (-len(pair[1]), pair[0])):
            sample = items[-1]
            rows.append({
                "class": kind,
                "count": len(items),
                "new_class": kind not in seen,
                "new_errors": sum(
                    1 for item in items if until is None or (item.result.stamp or "") > until
                ),
                "sample": {
                    "session": sample.result.session,
                    "at": sample.result.stamp,
                    "tool_use_id": sample.result.tool_use_id,
                    "input": shown_input(kind, command_of(sample.use)),
                    "error": sample.result.text[:400],
                    "script_error": sample.context[:1],
                },
            })
        projects[project] = {
            "results": totals[project],
            "errors": sum(sessions.values()),
            "sessions": dict(sessions.most_common()),
            "classes": rows,
        }
    return projects


def _render(summary: dict[str, Any]) -> str:
    lines = [
        f"orch errors since {summary['since']}: {summary['errors']} errors"
        f" in {summary['results']} tool results"
        f" ({summary['script_throws']} script throws merged,"
        f" {summary['undated']} undated errors skipped)",
        f"new classes since last run: {summary['new_classes']}",
    ]
    for project, data in summary["projects"].items():
        sessions = ", ".join(f"{name} {count}" for name, count in data["sessions"].items())
        lines.append(
            f"\n{project}: {data['errors']} errors in {data['results']} results ({sessions})"
        )
        for row in data["classes"]:
            mark = " NEW" if row["new_class"] else ""
            sample = row["sample"]
            first = next((line for line in sample["error"].splitlines() if line.strip()), "")
            lines.append(f"  {row['count']:>4}  {row['class']}{mark}")
            lines.append(f"        {sample['session']} {sample['at']} {sample['tool_use_id']}")
            lines.append(f"        input: {sample['input'][:160]}")
            lines.append(f"        error: {first[:160]}")
    return "\n".join(lines)


def _midnight() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT00:00:00Z")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="orch errors",
        description="Count tool and script errors in Claude Code session transcripts by class.",
    )
    parser.add_argument(
        "--root", default=str(Path.home() / ".claude" / "projects"), help="transcript root folder"
    )
    parser.add_argument(
        "--workspace", default=str(Path.cwd()), help="workspace folder whose sessions to read"
    )
    parser.add_argument("--since", default=None, help="UTC ISO time, default today 00:00")
    parser.add_argument(
        "--until", default="9999", help="UTC ISO time, end of the window, default no end"
    )
    parser.add_argument("--state", default=None, help="state file for classes seen in earlier runs")
    parser.add_argument(
        "--no-save", action="store_true", help="read the state file without updating it"
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    root = Path(args.root)
    if not root.is_dir():
        print(
            f"orch errors: no transcript folder {root};"
            " pass --root with the Claude Code projects folder",
            file=sys.stderr,
        )
        return 2
    since = args.since or _midnight()
    if not re.match(r"^\d{4}-\d{2}-\d{2}(T[\d:.]+Z?)?$", since):
        print(
            f"orch errors: --since {since!r} is not a UTC ISO time; use 2026-11-19T00:00:00Z",
            file=sys.stderr,
        )
        return 2
    state_path = Path(args.state) if args.state else None
    try:
        state = load_state(state_path)
    except ValueError as problem:
        print(f"orch errors: {problem}", file=sys.stderr)
        return 2
    seen = set(state["classes"])
    uses, results = collect(root, str(Path(args.workspace).resolve()))
    found, undated, script_throws = incidents(uses, results, since, args.until)
    totals = Counter(
        result.project
        for result in results.values()
        if result.stamp and since <= result.stamp < args.until
    )
    projects = report(found, totals, seen, state.get("until"))
    new_classes = sorted({item.kind for item in found} - seen)
    summary = {
        "since": since,
        "results": sum(totals.values()),
        "errors": len(found),
        "script_throws": script_throws,
        "undated": undated,
        "new_classes": len(new_classes) if state_path else "no --state given",
        "projects": projects,
    }
    print(json.dumps(summary, indent=1) if args.json else _render(summary))
    if state_path and not args.no_save:
        latest = max(
            (item.result.stamp or "" for item in found), default=state.get("until") or since
        )
        save_state(state_path, seen | {item.kind for item in found}, latest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
