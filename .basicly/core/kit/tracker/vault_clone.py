from __future__ import annotations

import hashlib
import importlib.util
import os
import shutil
import sys
import time
from collections.abc import Callable, Mapping
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
        raise ImportError(f"the tracker kit's {file_name} is missing from beside vault_clone.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


git_runner = _load("git_runner.py", "basicly_tracker_kit_git_runner")
events = _load("events.py", "basicly_tracker_kit_events")
vault_shapes = _load("vault_shapes.py", "basicly_tracker_kit_vault_shapes")

STATE_NAME = "basicly-tracker"
STATE_VARIABLE = "BASICLY_TRACKER_STATE"
FETCH_REFSPEC = "+refs/heads/main:refs/remotes/origin/main"
HEAD_REF = "refs/remotes/origin/main"
SEEN_REF = "refs/basicly/seen"
FETCHED_NAME = "fetched"
LOCK_WAIT = 2 * git_runner.DEFAULT_TIMEOUT + 10

Runner = Callable[..., str]


class CloneError(ValueError):
    pass


@dataclass(frozen=True)
class Clone:
    url: str
    folder: Path
    allow_file: bool

    @property
    def bare(self) -> Path:
        return self.folder / "clone"


def state_root(environ: Mapping[str, str], home: Path, *, windows: bool) -> Path:

    named = environ.get(STATE_VARIABLE, "")
    if named:
        if not Path(named).is_absolute():
            raise CloneError(f"{STATE_VARIABLE} must be an absolute path; it is {named!r}")
        return Path(named)
    base = environ.get("LOCALAPPDATA" if windows else "XDG_STATE_HOME", "")
    if base and Path(base).is_absolute():
        return Path(base) / STATE_NAME
    return home / ".local" / "state" / STATE_NAME


def vault_source(text: str) -> tuple[str, bool]:

    path = Path(text)
    if path.is_dir():
        return path.resolve().as_uri(), True
    try:
        return git_runner.remote_url(text), False
    except git_runner.GitValueError:
        raise CloneError(
            f"--vault {text!r} is neither a local repository folder nor an https://, "
            "ssh:// or user@host:path address"
        ) from None


def locate(state: Path, vault: str) -> Clone:

    url, allow_file = vault_source(vault)
    name = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
    return Clone(url, Path(state) / "vaults" / name, allow_file)


def _private(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        path.chmod(0o700)


def _ensure(clone: Clone, runner: Runner, timeout: float) -> None:

    if (clone.bare / "HEAD").is_file():
        return
    partial = clone.folder / "clone.partial"
    if partial.exists():
        shutil.rmtree(partial)
    runner(["init", "--bare", "-q", str(partial)], clone.folder, timeout=timeout)
    runner(["config", "remote.origin.url", clone.url], partial, timeout=timeout)
    runner(["config", "remote.origin.fetch", FETCH_REFSPEC], partial, timeout=timeout)
    partial.rename(clone.bare)


def _ref(clone: Clone, name: str, runner: Runner, timeout: float) -> str:
    try:
        return runner(["rev-parse", "--verify", "-q", name], clone.bare, timeout=timeout).strip()
    except git_runner.GitCommandError:
        return ""


def _fetch_twice(clone: Clone, runner: Runner, timeout: float) -> bool:
    argv = ["fetch", "--quiet", "--no-tags", "origin", FETCH_REFSPEC]
    for _attempt in range(2):
        try:
            runner(argv, clone.bare, timeout=timeout, allow_file=clone.allow_file)
        except git_runner.GitRunnerError:
            continue
        return True
    return False


def _advance_seen(clone: Clone, head: str, runner: Runner, timeout: float) -> dict[str, object]:

    seen = _ref(clone, SEEN_REF, runner, timeout)
    chain = vault_shapes.first_parent_chain(clone.bare, head)
    if seen and seen not in chain:
        return {"seen": seen, "moved": False, "retracted": True, "quarantined": []}
    new = chain[chain.index(seen) + 1 :] if seen else chain
    verdicts = [vault_shapes.classify(clone.bare, commit) for commit in new]
    if head != seen:
        runner(["update-ref", SEEN_REF, head, seen or "0" * len(head)], clone.bare, timeout=timeout)
    return {
        "seen": head,
        "moved": head != seen,
        "retracted": False,
        "quarantined": [{"commit": v.commit, "rule": v.rule} for v in verdicts if not v.valid],
    }


def fetch(
    clone: Clone,
    *,
    runner: Runner = git_runner.run,
    timeout: float = git_runner.DEFAULT_TIMEOUT,
    lock_wait: float = LOCK_WAIT,
    clock: Callable[[], float] = time.time,
) -> dict[str, object]:

    _private(clone.folder.parent.parent)
    _private(clone.folder)
    stamp = clone.folder / FETCHED_NAME
    with events.LedgerLock(clone.folder / "fetch", timeout_s=lock_wait):
        _ensure(clone, runner, timeout)
        fetched = _fetch_twice(clone, runner, timeout)
        head = _ref(clone, HEAD_REF, runner, timeout)
        if not head:
            raise CloneError(f"could not fetch {clone.url} and no earlier fetch exists; retry")
        if fetched:
            stamp.write_text(f"{clock()}\n", encoding="utf-8")
            seen = _advance_seen(clone, head, runner, timeout)
        else:
            seen = {"seen": _ref(clone, SEEN_REF, runner, timeout), "moved": False}
    age = max(0.0, clock() - float(stamp.read_text(encoding="utf-8")))
    return {
        "clone": str(clone.bare),
        "head": head,
        "fetched": fetched,
        "age_s": round(age, 1),
        **seen,
    }
