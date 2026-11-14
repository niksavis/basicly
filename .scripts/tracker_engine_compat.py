from __future__ import annotations

import argparse
import contextlib
import importlib
import io
import json
import os
import shlex
import shutil
import subprocess  # nosec B404 - the proof reads a local released source archive
import sys
import tarfile
from pathlib import Path, PurePosixPath
from typing import Any

from check_tracker_ledger_compat import (
    COMMAND,
    CRITERION,
    KIT,
    LEDGER,
    ROOT,
    baseline,
    checked,
    environment,
    fixed_ids,
    run,
)


def _engine(tag: str, target: Path) -> None:
    if not tag or tag.startswith("-"):
        raise ValueError("the engine baseline must name a local tag or commit")
    git = shutil.which("git")
    if git is None:
        raise ValueError("git is required for the local engine baseline")
    archived = subprocess.run(  # nosec B603 - fixed archive argv uses the checked local baseline
        [str(Path(git).resolve()), "archive", "--format=tar", tag, "src/basicly", ".basicly/core"],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    if archived.returncode:
        raise ValueError("the engine baseline could not be read; name a valid local tag or commit")
    with tarfile.open(fileobj=io.BytesIO(archived.stdout)) as archive:
        for member in archive.getmembers():
            if member.isdir():
                continue
            name = PurePosixPath(member.name)
            if not member.isfile() or name.is_absolute() or ".." in name.parts:
                raise ValueError("the engine baseline contains an unsafe source path")
            path = target.joinpath(*name.parts)
            path.parent.mkdir(parents=True, exist_ok=True)
            stream = archive.extractfile(member)
            if stream is None:
                raise ValueError("the engine baseline has an unreadable source file")
            path.write_bytes(stream.read())


def _hook_checks(repository: Path, record: str, paths: list[Path]) -> dict[str, object]:
    catalog = importlib.import_module("basicly.catalog")
    source = catalog.bundled_catalog_root() / "hooks/tracker-claim.py"
    hook = repository / ".git/hooks/commit-msg"
    hook.write_text(
        "#!/bin/sh\nexec " + shlex.join([sys.executable, str(source)]) + ' "$@"\n',
        encoding="utf-8",
    )
    hook.chmod(0o755)
    (repository / "change.txt").write_text("A checked engine consumer change\n", encoding="utf-8")
    env = dict(os.environ)
    checked(
        ["git", "add", "--", "change.txt", *(str(path.relative_to(repository)) for path in paths)],
        repository,
        env,
    )
    missing = run(["git", "commit", "-q", "-m", "fix: no record"], repository, env)
    if missing.returncode != 1 or "record" not in missing.stderr:
        raise ValueError("the actual engine claim hook accepted a commit without a record")
    held = run(
        ["git", "commit", "-q", "-m", f"fix: retain engine behavior ({record})"], repository, env
    )
    if held.returncode:
        raise ValueError("the actual engine claim hook refused the held record: " + held.stderr)
    return {
        "missing_exit": missing.returncode,
        "missing_stderr": missing.stderr,
        "held_exit": held.returncode,
        "held_stderr": held.stderr,
    }


def _offered_forms(calls: list[dict[str, Any]]) -> list[str]:
    mirror = importlib.import_module("basicly.mirror")
    usage = importlib.import_module("basicly.tracker_usage")
    offered = set(mirror._MIRRORED_WRITES) | {"review", "confirm"}
    exercised = {usage.split_invocation(list(row["argv"]))[0] for row in calls if not row["read"]}
    if exercised != offered or not {"create", "comments add"} <= offered:
        raise ValueError("the engine proof does not cover every offered write form")
    queries = importlib.import_module("basicly.tracker_query")
    offered_reads = set(queries.HANDLERS) - {"serve"}
    exercised_reads = {row["argv"][0] for row in calls if row["read"]}
    if exercised_reads != offered_reads or not {"list", "show"} <= offered_reads:
        raise ValueError("the engine proof does not cover every offered read command")
    return sorted(offered)


def worker(repository: Path) -> dict[str, Any]:
    cli = importlib.import_module("basicly.cli")
    owned_store = importlib.import_module("basicly.owned_store")
    state = importlib.import_module("basicly.state")

    os.chdir(repository)
    state.write_install_state(repository / ".basicly/state/install.json", "0.45.0", {})
    ledger = repository / LEDGER
    ledger.mkdir(parents=True)
    (ledger / "template.json").write_text(json.dumps({"prefix": "proof"}), encoding="utf-8")
    commands = owned_store.kit(repository, "commands")
    fixed_ids(commands.events)
    checked(
        ["git", "add", KIT.as_posix(), str(Path(LEDGER) / "template.json")],
        repository,
        dict(os.environ),
    )
    checked(
        ["git", "commit", "-q", "-m", "chore: install the consumer kit"],
        repository,
        dict(os.environ),
    )
    calls: list[dict[str, Any]] = []

    def invoke(*argv: str, read: bool = False) -> None:
        commands.events.wall_clock = lambda clock=None: (
            clock or (lambda: 1767225600.0 + len(calls) * 60)
        )
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = cli.main(["tracker", *([] if read else ["write", "--"]), *argv])
        calls.append({
            "argv": argv,
            "exit": code,
            "stdout": output.getvalue(),
            "stderr": errors.getvalue(),
            "read": read,
        })
        if code:
            raise ValueError(f"engine {argv[0]} failed: {output.getvalue()}{errors.getvalue()}")

    invoke(
        "create",
        "Keep engine writes usable",
        "--type",
        "task",
        "--description",
        "When an engine changes, I want my ledger writes to work, so I can continue.",
        "--acceptance",
        CRITERION,
    )
    record = next(iter(commands.events.fold(commands.events.read_events(ledger)[0]).records))
    invoke("create", "Dependency control", "--type", "task")
    other = next(
        name
        for name in commands.events.fold(commands.events.read_events(ledger)[0]).records
        if name != record
    )
    invoke("dep", "add", record, other, "--type", "blocks")
    invoke("dep", "remove", record, other, "--type", "blocks")
    observed = run(COMMAND, repository, dict(os.environ))
    if observed.returncode or observed.stdout.strip() != "saved":
        raise ValueError("the actual engine gate command failed")
    invoke(
        "gate", "report", record, "--gate", "verify", "--provider", "consumer", "--status", "pass"
    )
    invoke("comments", "add", record, "The engine keeps old kit behavior.")
    invoke("update", record, "--priority", "1")
    invoke(
        "review",
        record,
        "--evidence",
        json.dumps({"checks": [{"criterion": CRITERION, "command": COMMAND, "expected": "saved"}]}),
    )
    invoke("update", record, "--assignee", "consumer", "--status", "in_progress")
    observed = run(COMMAND, repository, dict(os.environ))
    if observed.returncode or observed.stdout.strip() != "saved":
        raise ValueError("the engine confirmation command failed")
    invoke(
        "confirm",
        record,
        "--evidence",
        json.dumps({
            "checks": [
                {
                    "criterion": CRITERION,
                    "command": COMMAND,
                    "result": observed.stdout,
                    "exit_code": observed.returncode,
                }
            ]
        }),
    )
    invoke("close", record, "--reason", "The real command printed saved")
    for argv in (
        ("list",),
        ("show", record),
        ("ready", "--json"),
        ("blocked", "--json"),
        ("stats", "--json"),
        ("items", "--json"),
        ("describe", "--json"),
    ):
        invoke(*argv, read=True)
    found, unreadable = commands.events.read_events(ledger)
    if unreadable:
        raise ValueError("the engine produced unreadable history")
    return {
        "calls": calls,
        "forms": _offered_forms(calls),
        "hook": _hook_checks(repository, record, commands.events.ledger_paths(ledger)),
        "events": [json.loads(commands.events.to_json(event)) for event in found],
    }


def compare(tag: str, output: Path) -> dict[str, object]:
    output.mkdir(parents=True, exist_ok=False)
    _engine(tag, output / "baseline-engine")
    env = environment(output)
    reports = {}
    for name, source in (("released", output / "baseline-engine/src"), ("candidate", ROOT / "src")):
        repository = output / (name + "-repository")
        repository.mkdir()
        checked(["git", "init", "-q", "-b", "main"], repository, env)
        baseline(tag, repository / KIT)
        done = run(
            [sys.executable, str(Path(__file__).resolve()), "--worker", str(repository)],
            repository,
            dict(env, PYTHONPATH=str(source)),
        )
        (output / (name + ".stdout")).write_text(done.stdout, encoding="utf-8")
        (output / (name + ".stderr")).write_text(done.stderr, encoding="utf-8")
        if done.returncode:
            raise ValueError(f"{name} engine proof failed: {done.stdout}{done.stderr}")
        reports[name] = json.loads(done.stdout)
    (output / "reports.json").write_text(json.dumps(reports, indent=2), encoding="utf-8")
    if reports["released"] != reports["candidate"]:
        raise ValueError(
            "engine-driven reports or events differ from the released engine with its old kit"
        )
    return {
        "result": "pass",
        "forms": len(reports["candidate"]["forms"]),
        "reads": sum(bool(row["read"]) for row in reports["candidate"]["calls"]),
        "hook": "actual uncited refusal and held-record Git commit passed",
        "comparison": "engine-driven reports and events match exactly",
        "baseline": tag,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(worker(args.worker.resolve())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
