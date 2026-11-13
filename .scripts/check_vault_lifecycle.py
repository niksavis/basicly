from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from check_tracker_ledger_compat import (
    COMMAND,
    CRITERION,
    KIT,
    LEDGER,
    ROOT,
    checked,
    copy_kit,
    run,
)

MARKER = ".tracker-consumer-proof.json"


def load(kit: Path) -> Any:
    spec = importlib.util.spec_from_file_location("vault_consumer_cli", kit / "cli.py")
    if spec is None or spec.loader is None:
        raise ValueError("--kit must contain the standalone cli.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def local_vault(kit: Any, repository: Path) -> tuple[str, str]:
    config = kit.vault_config.load(repository)
    if config is None:
        raise ValueError("--repository needs a migrated scratch vault config")
    url, local = kit.vault_cli.vault_clone.vault_source(config.vault)
    if not local:
        raise ValueError("this consumer proof only writes to a local scratch vault")
    return url, config.prefix


def history(kit: Any, repository: Path, record: str) -> list[dict[str, Any]]:
    ledger = kit.vault_cli.read_ledger(repository / LEDGER, time.time)
    return [
        json.loads(kit.events.to_json(event))
        for event in kit.events.read_events(ledger)[0]
        if event.record == record
        and not (
            event.actor == "vault"
            and event.kind == kit.events.KIND_FIELD
            and event.payload.get("name") == kit.events.HOLDER_FIELD
        )
    ]


def unchanged(before: list[dict[str, Any]], after: list[dict[str, Any]]) -> None:
    current = {event["id"]: event for event in after}
    if any(current.get(event["id"]) != event for event in before):
        raise ValueError("an earlier migrated event changed or disappeared")


def local_logs(repository: Path) -> dict[str, str]:
    return {path.name: path.read_bytes().hex() for path in (repository / LEDGER).glob("*.jsonl")}


def caller(kit: Path, repository: Path, transcript: list[dict[str, Any]]) -> Any:
    def invoke(*argv: str) -> dict[str, Any]:
        done = run([sys.executable, "-S", str(kit / "cli.py"), *argv], repository, dict(os.environ))
        transcript.append({"argv": list(argv), "exit": done.returncode})
        if done.returncode:
            raise ValueError(f"{argv[0]} exited {done.returncode}: {done.stdout}{done.stderr}")
        return json.loads(done.stdout)

    return invoke


def fields() -> list[str]:
    return [
        "--title",
        "Standalone vault lifecycle control",
        "--field",
        "issue_type=task",
        "--field",
        "size=small",
        "--description",
        "When a reviewer checks a migrated control, I want saved evidence, "
        "so I can prove its lifecycle.",
        "--acceptance",
        CRITERION,
    ]


def prepare(kit: Path, repository: Path, prefix: str) -> None:
    if repository.exists():
        raise ValueError("--repository already exists; choose a new control repository")
    repository.mkdir(parents=True)
    checked(["git", "init", "-q", str(repository)], repository, dict(os.environ))
    copy_kit(kit, repository / ".basicly/kit/tracker")
    transcript: list[dict[str, Any]] = []
    invoke = caller(kit, repository, transcript)
    record = invoke("create", LEDGER, "--prefix", prefix, *fields())["record"]
    invoke("comment", LEDGER, record, "This control existed before its migration.")
    source = load(kit)
    expected = [
        json.loads(source.events.to_json(event))
        for event in source.events.read_events(repository / LEDGER)[0]
        if event.record == record
    ]
    marker = {"record": record, "prefix": prefix, "criterion": CRITERION, "history": expected}
    (repository / MARKER).write_text(json.dumps(marker, indent=2), encoding="utf-8")
    print(
        json.dumps({
            "result": "prepared",
            "repository": str(repository),
            "ledger": LEDGER,
            "record": record,
            "prefix": prefix,
            "source_events": len(expected),
            "next": "migrate this control into the workspace scratch vault, then run check",
        })
    )


def lifecycle(invoke: Any, record: str, repository: Path) -> dict[str, Any]:
    invoke("claim", record)
    invoke("update", LEDGER, record, "--field", "priority=1")
    invoke("comment", LEDGER, record, "The standalone lifecycle keeps this control's history.")
    plan = {"checks": [{"criterion": CRITERION, "command": COMMAND, "expected": "saved"}]}
    invoke("review", LEDGER, record, "--evidence", json.dumps(plan))
    observed = run(COMMAND, repository, dict(os.environ))
    if observed.returncode or observed.stdout.strip() != "saved":
        raise ValueError("the actual confirmation command did not print saved")
    evidence = {
        "checks": [
            {
                "criterion": CRITERION,
                "command": COMMAND,
                "result": observed.stdout,
                "exit_code": observed.returncode,
            }
        ]
    }
    invoke("confirm", LEDGER, record, "--evidence", json.dumps(evidence))
    invoke(
        "close",
        LEDGER,
        record,
        "--reason",
        "The standalone consumer command printed saved with exit zero",
    )
    shown = invoke("show", LEDGER, record)
    if shown["status"] != "closed" or shown["fields"]["priority"] != 1:
        raise ValueError("the standalone control did not complete")
    return shown


@dataclass(frozen=True)
class WorkspaceControl:
    repository: Path
    record: str
    before: list[dict[str, Any]]
    priority: int
    priority_semantic: bool


def inspect_workspace(
    kit: Any, invoke: Any, repository: Path, record: str, control_events: int
) -> WorkspaceControl:
    before = history(kit, repository, record)
    shown = invoke("show", LEDGER, record)
    if len(before) <= control_events or not shown.get("process", {}).get("review_current"):
        raise ValueError("the real workspace control needs a longer history and a current review")
    template_folder = kit.vault_cli.read_ledger(repository / LEDGER, time.time)
    process = kit.commands.review.process
    template = kit.commands.templates.load(template_folder)
    headings = process.shaping.required(shown["fields"], template)
    semantic = process.SEMANTIC_FIELDS | frozenset(
        process.shaping.field_of(heading) for heading in headings
    )
    priority = shown["fields"].get("priority", 2)
    if not isinstance(priority, int) or isinstance(priority, bool) or priority not in range(5):
        raise ValueError("the real workspace control has no valid priority")
    return WorkspaceControl(repository, record, before, priority, "priority" in semantic)


def real_record(kit: Any, invoke: Any, control: WorkspaceControl) -> dict[str, Any]:
    repository, record, before = control.repository, control.record, control.before
    changed = (control.priority + 1) % 5
    note = "Scratch-only consumer proof " + uuid.uuid4().hex
    logs = local_logs(repository)
    invoke("comment", LEDGER, record, note)
    invoke("update", LEDGER, record, "--field", f"priority={changed}")
    pending = history(kit, repository, record)
    unchanged(before, pending)
    initial = {event["id"] for event in before}
    new = [event for event in pending if event["id"] not in initial]
    if (
        len(new) != 2
        or sum(
            event["kind"] == kit.events.KIND_NOTE and event["payload"].get("text") == note
            for event in new
        )
        != 1
    ):
        raise ValueError("the two workspace writes were not recorded exactly once")
    invoke("sync", "--urgent")
    after = history(kit, repository, record)
    unchanged(before, after)
    if after != pending:
        raise ValueError("sharing changed or duplicated the workspace write history")
    result = invoke("show", LEDGER, record)
    if result["fields"].get("priority") != changed:
        raise ValueError("the real workspace priority write is absent")
    expected_review = not control.priority_semantic
    if result["process"]["review_current"] != expected_review:
        raise ValueError(
            "the real workspace review state differs from its declared semantic fields"
        )
    if local_logs(repository) != logs:
        raise ValueError("the real workspace consumer wrote local ledger events")
    return {
        "earlier_events": len(before),
        "history_byte_equal": True,
        "new_events": len(new),
        "review": "current"
        if expected_review
        else "stale: the configured template makes priority semantic",
        "confirmed_or_closed": False,
    }


def validate_history(kit: Any, marker: dict[str, Any], record: str) -> None:
    if not isinstance(marker.get("history"), list) or not marker["history"]:
        raise ValueError("the control marker needs its original event history")
    try:
        for event in marker["history"]:
            parsed = kit.events.from_json(json.dumps(event))
            if parsed.record != record:
                raise ValueError("the control marker history names another record")
    except kit.events.LedgerError:
        raise ValueError("the control marker history is invalid") from None


def check(
    kit_path: Path, repository: Path, workspace: Path, workspace_record: str, output: Path
) -> None:
    if output.exists():
        raise ValueError("--out already exists; choose a new report path")
    marker_path = repository / MARKER
    if not marker_path.is_file():
        raise ValueError("--repository lacks the prepared control marker; run prepare first")
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    if (
        not isinstance(marker, dict)
        or marker.get("criterion") != CRITERION
        or not isinstance(marker.get("record"), str)
    ):
        raise ValueError("the control marker does not describe this executable proof")
    kit = load(kit_path)
    control_vault, prefix = local_vault(kit, repository)
    workspace_vault, workspace_prefix = local_vault(kit, workspace)
    if control_vault != workspace_vault:
        raise ValueError("the control and workspace copy must use the same local scratch vault")
    if prefix == workspace_prefix:
        raise ValueError("the control and workspace project prefixes must be different")
    record = marker["record"]
    kit.ids.validate_record_id(record)
    kit.ids.validate_record_id(workspace_record)
    if (
        marker.get("prefix") != prefix
        or not record.startswith(prefix + "-")
        or not workspace_record.startswith(workspace_prefix + "-")
    ):
        raise ValueError("the control and workspace ids must match their separate project prefixes")
    validate_history(kit, marker, record)
    before = history(kit, repository, record)
    unchanged(marker["history"], before)
    logs = local_logs(repository)
    transcript: list[dict[str, Any]] = []
    invoke = caller(kit_path, repository, transcript)
    initial = invoke("show", LEDGER, record)
    if initial["status"] == "closed" or initial["fields"].get("acceptance_criteria") != CRITERION:
        raise ValueError("use a fresh migrated control with the executable criterion")
    workspace_invoke = caller(kit_path, workspace, transcript)
    prepared = inspect_workspace(kit, workspace_invoke, workspace, workspace_record, len(before))
    lifecycle(invoke, record, repository)
    unchanged(before, history(kit, repository, record))
    born = invoke("create", LEDGER, *fields())["record"]
    lifecycle(invoke, born, repository)
    invoke("sync", "--urgent")
    unchanged(before, history(kit, repository, record))
    if local_logs(repository) != logs:
        raise ValueError("the standalone consumer wrote local ledger events")
    real = real_record(kit, workspace_invoke, prepared)
    result = {
        "result": "pass",
        "migrated_control": record,
        "vault_born_control": born,
        "source_events_unchanged": len(before),
        "local_ledger_writes": 0,
        "real_workspace_control": real,
        "transcript": transcript,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as report:
        report.write(json.dumps(result, indent=2))
    print(json.dumps({key: value for key, value in result.items() if key != "transcript"}))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check standalone lifecycles in a local migrated scratch vault."
    )
    parser.add_argument("action", choices=["prepare", "check"])
    parser.add_argument("--kit", type=Path, default=ROOT / KIT)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--prefix", default="proof")
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--session", required=True)
    parser.add_argument("--workspace-repository", type=Path)
    parser.add_argument("--workspace-record")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    if not args.session.strip() or not args.session.isprintable():
        parser.error("--session must be a nonempty printable name")
    os.environ.update(
        BASICLY_TRACKER_STATE=str(args.state.resolve()),
        BASICLY_TRACKER_SESSION=args.session,
        BASICLY_HOLDER="consumer-proof",
        AI_AGENT="consumer-proof",
    )
    for variable in ("CI", "BASICLY_TRACKER_EPHEMERAL", "CLAUDECODE", "BR_AGENT_NAME"):
        os.environ.pop(variable, None)
    try:
        if args.action == "prepare":
            prepare(args.kit.resolve(), args.repository.resolve(), args.prefix)
        elif args.workspace_repository is None or not args.workspace_record or args.out is None:
            parser.error("check needs --workspace-repository, --workspace-record and a new --out")
        else:
            check(
                args.kit.resolve(),
                args.repository.resolve(),
                args.workspace_repository.resolve(),
                args.workspace_record,
                args.out.resolve(),
            )
    except (ValueError, OSError) as error:
        print(f"vault consumer proof refused: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
