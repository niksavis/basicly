from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import os
import random
import shutil
import subprocess  # nosec B404 - isolated consumer proof needs real cli and git processes
import sys
import tarfile
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
KIT = Path(".basicly/core/kit/tracker")
LEDGER = ".basicly/ledger"
PLACED = Path(".basicly/kit/tracker")
CRITERION = "When the check runs, it shall print saved."
COMMAND = ["python3", "-c", "print('saved')"]


def run(argv: list[str], root: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    done = subprocess.run(  # nosec B603 - internal argv lists execute without a shell
        argv, cwd=root, env=env, capture_output=True, check=False
    )
    return subprocess.CompletedProcess(
        argv, done.returncode, done.stdout.decode("utf-8"), done.stderr.decode("utf-8")
    )


def checked(argv: list[str], root: Path, env: dict[str, str]) -> str:
    done = run(argv, root, env)
    if done.returncode:
        raise ValueError(f"{argv[0]} exited {done.returncode}: {done.stdout}{done.stderr}")
    return done.stdout


def copy_kit(source: Path, destination: Path) -> None:
    if not (source / "cli.py").is_file():
        raise ValueError("--kit must contain the standalone cli.py")
    if any(path.is_symlink() for path in source.rglob("*")):
        raise ValueError("the kit contains a symlink; use a regular source snapshot")
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns("__pycache__", ".env*"))


def baseline(tag: str, destination: Path) -> None:
    if not tag or tag.startswith("-"):
        raise ValueError("--baseline must name a local tag or commit")
    git = shutil.which("git")
    if git is None:
        raise ValueError("git is required for the local baseline snapshot")
    made = subprocess.run(  # nosec B603 - fixed git archive argv with validated local revision
        [git, "archive", "--format=tar", tag, KIT.as_posix()],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    if made.returncode:
        raise ValueError(f"git archive exited {made.returncode}")
    prefix = PurePosixPath(KIT.as_posix())
    with tarfile.open(fileobj=io.BytesIO(made.stdout)) as archive:
        for member in archive.getmembers():
            if member.isdir():
                continue
            relative = PurePosixPath(member.name).relative_to(prefix)
            if not member.isfile() or ".." in relative.parts or member.name.startswith("/"):
                raise ValueError("the baseline archive contains a non-regular kit path")
            target = destination.joinpath(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            stream = archive.extractfile(member)
            if stream is None:
                raise ValueError("the baseline archive contains an unreadable kit file")
            target.write_bytes(stream.read())


def environment(output: Path) -> dict[str, str]:
    config = output / "gitconfig"
    config.write_text("", encoding="utf-8")
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(
        GIT_CONFIG_GLOBAL=str(config),
        GIT_CONFIG_NOSYSTEM="1",
        GIT_AUTHOR_NAME="consumer",
        GIT_COMMITTER_NAME="consumer",
        GIT_AUTHOR_EMAIL="consumer@example.invalid",
        GIT_COMMITTER_EMAIL="consumer@example.invalid",
        GIT_AUTHOR_DATE="2026-01-01T00:00:00+00:00",
        GIT_COMMITTER_DATE="2026-01-01T00:00:00+00:00",
        BASICLY_HOLDER="consumer",
        AI_AGENT="consumer",
    )
    for key in ("CLAUDECODE", "BR_AGENT_NAME", "CI", "BASICLY_TRACKER_EPHEMERAL"):
        env.pop(key, None)
    return env


def invoker(
    repository: Path, real: bool, transcript: list[dict[str, Any]]
) -> Callable[..., dict[str, Any]]:
    env = dict(os.environ)
    source = repository / PLACED / "cli.py"
    kit: Any = None
    if not real:
        spec = importlib.util.spec_from_file_location("consumer_tracker_cli", source)
        if spec is None or spec.loader is None:
            raise ValueError("the consumer kit cannot load")
        kit = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = kit
        spec.loader.exec_module(kit)
        if not hasattr(kit.events, "wall_clock") or not hasattr(kit.events.ids, "_SYSTEM_RANDOM"):
            raise ValueError("the kit lacks the wall_clock or id _SYSTEM_RANDOM seam")
        kit.events.ids._SYSTEM_RANDOM = random.Random(37)  # nosec B311 - repeatable proof ids only

    def invoke(*argv: str) -> dict[str, Any]:
        if real:
            done = run([sys.executable, "-S", str(source), *argv], repository, env)
            code, stdout, stderr = done.returncode, done.stdout, done.stderr
        else:
            stamp = 1767225600.0 + 60 * len(transcript)
            kit.events.wall_clock = lambda clock=None: clock or (lambda: stamp)
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = kit.main(list(argv))
            stdout, stderr = out.getvalue(), err.getvalue()
        row = {"argv": list(argv), "exit": code, "stdout": stdout, "stderr": stderr}
        transcript.append(row)
        if code:
            raise ValueError(f"{argv[0]} exited {code}: {stdout}{stderr}")
        return json.loads(stdout)

    return invoke


def worker(repository: Path, real: bool) -> dict[str, Any]:
    env = dict(os.environ)
    os.chdir(repository)
    source = repository / PLACED / "cli.py"
    transcript: list[dict[str, Any]] = []
    invoke = invoker(repository, real, transcript)
    installed = checked(
        [
            sys.executable,
            "-S",
            str(source.with_name("install_hook.py")),
            "--root",
            ".",
            "--ledger",
            LEDGER,
        ],
        repository,
        env,
    )
    if not (repository / ".git/hooks/commit-msg").is_file():
        raise ValueError("the real commit-msg claim hook was not installed")
    record = invoke(
        "create",
        LEDGER,
        "--prefix",
        "proof",
        "--title",
        "Retain the old ledger lifecycle",
        "--field",
        "issue_type=task",
        "--field",
        "size=small",
        "--description",
        "When a repository keeps its ledger, I want its writes to work, so I can upgrade safely.",
        "--acceptance",
        CRITERION,
    )["record"]
    offered = invoke("ready", LEDGER)
    if record not in {row["record"] for row in offered["records"]}:
        raise ValueError("the created small task is not offered by ready")
    invoke("claim", LEDGER, record)
    invoke("comment", LEDGER, record, "The consumer keeps the ordinary ledger.")
    invoke("update", LEDGER, record, "--field", "priority=1")
    plan = {"checks": [{"criterion": CRITERION, "command": COMMAND, "expected": "saved"}]}
    invoke("review", LEDGER, record, "--evidence", json.dumps(plan))
    observed = run(COMMAND, repository, env)
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
        "The actual consumer command printed saved with exit zero",
    )
    shown = invoke("show", LEDGER, record)
    if shown["status"] != "closed" or shown["fields"]["priority"] != 1:
        raise ValueError("the final consumer record has the wrong status or priority")
    if record in {row["record"] for row in invoke("ready", LEDGER)["records"]}:
        raise ValueError("ready still offers the closed consumer task")
    if invoke("fsck", LEDGER)["exit_code"] != 0:
        raise ValueError("the consumer ledger failed unchanged fsck")
    if (repository / ".basicly-tracker.toml").exists():
        raise ValueError("the old-ledger consumer acquired a vault config")
    (repository / "change.txt").write_text(
        "The consumer change is checked by the real claim hook.\n", encoding="utf-8"
    )
    logs = sorted((repository / LEDGER).glob("*.jsonl"))
    if not logs:
        raise ValueError("the ordinary consumer wrote no ledger events")
    checked(
        ["git", "add", "--", "change.txt", *(str(path.relative_to(repository)) for path in logs)],
        repository,
        env,
    )
    refused = run(["git", "commit", "-m", "fix: a change without its record"], repository, env)
    if refused.returncode != 1 or "record" not in refused.stderr + refused.stdout:
        raise ValueError("the installed claim hook did not refuse the uncited code commit")
    committed = run(
        ["git", "commit", "-m", f"fix: retain the consumer ledger ({record})"], repository, env
    )
    if committed.returncode:
        raise ValueError(
            f"the real claimed Git commit failed: {committed.stdout}{committed.stderr}"
        )
    return {
        "transcript": transcript,
        "installed": installed,
        "ledger": {path.name: path.read_bytes().hex() for path in logs},
        "refused_commit": {
            "exit": refused.returncode,
            "stdout": refused.stdout,
            "stderr": refused.stderr,
        },
        "commit": {
            "exit": committed.returncode,
            "stdout": committed.stdout,
            "stderr": committed.stderr,
        },
        "subject": checked(["git", "log", "-1", "--format=%s"], repository, env),
    }


def compare(kit: Path, tag: str, output: Path) -> None:
    if output.exists():
        raise ValueError("--out already exists; choose a new output directory")
    if output.is_relative_to(kit):
        raise ValueError("--out must be outside the source kit")
    output.mkdir(parents=True)
    old = output / "baseline-kit"
    baseline(tag, old)
    env = environment(output)
    reports: dict[str, Any] = {}
    repository = output / "repository"
    for name, source, real in (
        ("released", old, False),
        ("candidate", kit, False),
        ("real-cli", kit, True),
    ):
        repository.mkdir()
        checked(["git", "init", "-q", "-b", "main", "."], repository, env)
        copy_kit(source, repository / PLACED)
        argv = [sys.executable, "-S", str(Path(__file__).resolve()), "--worker", str(repository)]
        if real:
            argv.append("--real")
        done = run(argv, repository, env)
        (output / (name + ".stdout")).write_text(done.stdout, encoding="utf-8")
        (output / (name + ".stderr")).write_text(done.stderr, encoding="utf-8")
        if done.returncode:
            raise ValueError(f"{name} consumer failed: {done.stdout}{done.stderr}")
        reports[name] = json.loads(done.stdout)
        repository.rename(output / (name + "-repository"))
    left, right = reports["released"], reports["candidate"]
    differences = [
        key for key in sorted(left.keys() | right.keys()) if left.get(key) != right.get(key)
    ]
    (output / "reports.json").write_text(json.dumps(reports, indent=2), encoding="utf-8")
    if differences:
        raise ValueError("candidate differs from released kit: " + ", ".join(differences))
    print(
        json.dumps({
            "result": "pass",
            "comparison": "complete reports and ledger events match byte for byte",
            "baseline": tag,
            "real_cli": "lifecycle, fsck and normal Git claim hook passed",
            "output": str(output),
        })
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare plain-ledger behavior with a released kit and run the real CLI."
    )
    parser.add_argument("--kit", type=Path, default=ROOT / KIT)
    parser.add_argument("--baseline", default="v0.45.0")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--worker", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--real", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        if args.worker is not None:
            print(json.dumps(worker(args.worker.resolve(), args.real)))
        elif args.out is None:
            parser.error("--out is required; use a new scratch directory")
        else:
            compare(args.kit.resolve(), args.baseline, args.out.resolve())
    except (ValueError, OSError) as error:
        print(f"consumer comparison refused: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
