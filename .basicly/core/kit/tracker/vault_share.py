from __future__ import annotations

import importlib.util
import json
import sys
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent


def _load(file_name: str, module_name: str) -> Any:

    cached = sys.modules.get(module_name)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(module_name, _HERE / file_name)
    if spec is None or spec.loader is None:
        raise ImportError(f"the tracker kit's {file_name} is missing from beside vault_share.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


git_runner = _load("git_runner.py", "basicly_tracker_kit_git_runner")
events = _load("events.py", "basicly_tracker_kit_events")
vault_clone = _load("vault_clone.py", "basicly_tracker_kit_vault_clone")
vault_queue = _load("vault_queue.py", "basicly_tracker_kit_vault_queue")

SEGMENT_BYTES = 1 << 20
BULK_ATTEMPTS = 5
SHARE_LOCK = "share"
SIGNED_ONLY = (
    "the vault refused unsigned commits; share commits are unsigned and authored by the writer "
    "id, so turn off required commit signatures for the vault repository"
)

Runner = Callable[..., str]


class ShareError(ValueError):
    pass


@dataclass(frozen=True)
class Plan:
    urgent: bool = False
    deadline_s: float = 10.0
    attempts: int = BULK_ATTEMPTS


def _text(lines: Sequence[Mapping[str, object]]) -> str:
    return "".join(json.dumps(line, sort_keys=True, ensure_ascii=False) + "\n" for line in lines)


def prune(clone: Any) -> list[dict[str, object]]:

    queue = clone.folder / vault_queue.QUEUE_NAME
    valid = vault_clone.valid_ids(clone)
    with events.LedgerLock(clone.folder):
        lines = vault_queue.pending(queue)
        keep = [line for line in lines if str(line.get("id")) not in valid]
        if len(keep) != len(lines):
            vault_clone.durable(queue, _text(keep))
    return keep


def _segment(git: Runner, head: str, writer: str) -> tuple:

    folder = f"{vault_clone.vault_shapes.LOG_ROOT}/{writer}/"
    listed = git(["ls-tree", "--name-only", head, folder]).split()
    if not listed:
        return f"{folder}00001.jsonl", ""
    path = max(listed)
    old = git(["cat-file", "blob", f"{head}:{path}"])
    if len(old.encode("utf-8")) < SEGMENT_BYTES:
        return path, old
    return f"{folder}{int(Path(path).stem) + 1:05d}.jsonl", ""


def _build(clone: Any, head: str, writer: str, batch: list, git: Runner) -> str:

    path, old = _segment(git, head, writer)
    header = (
        "" if old else _text([{"kind": vault_clone.vault_shapes.SEGMENT_KIND, "writer": writer}])
    )
    token = uuid.uuid4().hex
    blob_file = clone.folder / f"share-{token}.blob"
    index = clone.folder / f"share-{token}.index"
    env = {"GIT_INDEX_FILE": str(index)}
    try:
        blob_file.write_bytes((old + header + _text(batch)).encode("utf-8"))
        hashed = ["hash-object", "-w", "--no-filters", str(blob_file)]
        blob = git(hashed).strip()
        git(["read-tree", head], env=env)
        entry = ["update-index", "--add", "--cacheinfo", f"100644,{blob},{path}"]
        git(entry, env=env)
        tree = git(["write-tree"], env=env).strip()
    finally:
        for leftover in (blob_file, index, index.with_name(index.name + ".lock")):
            leftover.unlink(missing_ok=True)
    who = {"NAME": writer[:12], "EMAIL": f"{writer}@writers.basicly.invalid"}
    ident = {
        f"GIT_{role}_{key}": value for role in ("AUTHOR", "COMMITTER") for key, value in who.items()
    }
    made = [
        "-c",
        "commit.gpgSign=false",
        "commit-tree",
        tree,
        "-p",
        head,
        "-m",
        f"batch {len(batch)}",
    ]
    return git(made, env=ident).strip()


def _push(clone: Any, commit: str, runner: Runner, timeout: float) -> None:
    hooks = clone.folder / "no-hooks"
    hooks.mkdir(exist_ok=True)
    pushed = [
        "-c",
        f"core.hooksPath={hooks}",
        "push",
        "--quiet",
        "origin",
        f"{commit}:refs/heads/main",
    ]
    runner(pushed, clone.bare, timeout=timeout, allow_file=clone.allow_file)


def _budget(plan: Plan, started: float, clock: Callable[[], float]) -> float:
    if not plan.urgent:
        return git_runner.DEFAULT_TIMEOUT
    left = plan.deadline_s - (clock() - started)
    if left <= 0:
        raise ShareError(
            f"the urgent share passed its {plan.deadline_s:g} s deadline; run sync again"
        )
    return left


def _again(plan: Plan, attempt: int, started: float, clock: Callable[[], float]) -> bool:
    if plan.urgent:
        return clock() - started < plan.deadline_s
    return attempt < plan.attempts


def _stopped(head: str, stop: Mapping[str, object], clone: Any) -> dict[str, object]:
    payload = stop.get("payload")
    said = payload if isinstance(payload, Mapping) else {}
    return {
        "shared": 0,
        "head": head,
        "pending": len(vault_queue.pending(clone.folder / vault_queue.QUEUE_NAME)),
        "ended": {"reason": said.get("reason"), "successor": said.get("successor")},
    }


def share(
    clone: Any,
    state: Path,
    *,
    plan: Plan | None = None,
    runner: Runner = git_runner.run,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, object]:

    plan = plan or Plan()
    started = clock()
    writer = vault_queue.writer_of(state)
    wait = _budget(plan, started, clock) if plan.urgent else vault_clone.LOCK_WAIT
    with events.LedgerLock(clone.folder / SHARE_LOCK, timeout_s=wait):
        attempt = 0
        while True:
            attempt += 1
            budget = _budget(plan, started, clock)
            fetched = vault_clone.fetch(clone, runner=runner, timeout=budget, lock_wait=budget)
            head = str(fetched["head"])
            if fetched.get("retracted") or vault_clone.unrepaired(clone, runner, budget):
                raise ShareError(
                    "the vault head dropped commits this clone had seen; "
                    "run fsck --repair --vault with the same vault, then sync again"
                )
            stop = vault_clone.ended(clone)
            if stop is not None:
                return _stopped(head, stop, clone)
            batch = [line for line in prune(clone) if line.get("writer") == writer]
            if not batch:
                return {"shared": 0, "head": head, "pending": 0}

            def git(args: list[str], budget: float = budget, **options: Any) -> str:
                return runner(args, clone.bare, timeout=budget, **options)

            commit = _build(clone, head, writer, batch, git)
            try:
                _push(clone, commit, runner, budget)
            except git_runner.GitRunnerError as exc:
                if "signature" in str(getattr(exc, "stderr", "")).lower():
                    raise ShareError(SIGNED_ONLY) from None
                if _again(plan, attempt, started, clock):
                    continue
                raise ShareError(
                    f"the vault refused the share {attempt} times; "
                    f"{len(batch)} events stay queued; run sync again"
                ) from None
            vault_clone.settle(clone, runner=runner, timeout=budget, lock_wait=budget)
            return {"shared": len(batch), "head": commit, "pending": len(prune(clone))}
