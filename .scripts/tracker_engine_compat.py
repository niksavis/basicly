from __future__ import annotations

import argparse
import contextlib
import importlib
import io
import json
import os
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
    calls: list[dict[str, object]] = []

    def invoke(*argv: str) -> None:
        commands.events.wall_clock = lambda clock=None: (
            clock or (lambda: 1767225600.0 + len(calls) * 60)
        )
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = cli.main(["tracker", "write", "--", *argv])
        calls.append({
            "argv": argv,
            "exit": code,
            "stdout": output.getvalue(),
            "stderr": errors.getvalue(),
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
    invoke("comments", "add", record, "The engine keeps old kit behavior.")
    invoke("update", record, "--priority", "1")
    invoke(
        "review",
        record,
        "--evidence",
        json.dumps({"checks": [{"criterion": CRITERION, "command": COMMAND, "expected": "saved"}]}),
    )
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
    found, unreadable = commands.events.read_events(ledger)
    if unreadable:
        raise ValueError("the engine produced unreadable history")
    return {
        "calls": calls,
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
        "forms": len(reports["candidate"]["calls"]),
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
