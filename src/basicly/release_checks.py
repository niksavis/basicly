from __future__ import annotations

import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from basicly.checkout import run as checkout_run
from basicly.config import VerifyCheck, load_verify_config
from basicly.hook_runtime import load

LIVE_LEDGER = "pytest-live-ledger"
WORKERS = "4"
TAG_GLOB = "v[0-9]*"

Run = Callable[[Sequence[str], Path], int]


@dataclass(frozen=True)
class Outcome:
    lines: tuple[str, ...]
    passed: bool


def execute(argv: Sequence[str], cwd: Path) -> int:
    done = checkout_run(list(argv), cwd=cwd, check=False)
    if done.returncode != 0:
        sys.stdout.write(done.stdout + done.stderr)
    return done.returncode


def _git(root: Path, *args: str) -> str | None:
    done = checkout_run(["git", "-C", str(root), *args], check=False)
    return done.stdout if done.returncode == 0 else None


def record_checks(checks: Sequence[VerifyCheck]) -> list[VerifyCheck]:
    return [check for check in checks if check.local_only and check.name != LIVE_LEDGER]


def last_tag(root: Path) -> str:
    found = _git(root, "describe", "--tags", "--abbrev=0", "--match", TAG_GLOB)
    return found.strip() if found else ""


def live_ledger_reason(root: Path, check: VerifyCheck) -> tuple[bool, str]:
    tag = last_tag(root)
    if not tag:
        return True, "no release tag, so every input counts as changed"
    listed = _git(root, "diff", "--name-only", tag, "HEAD")
    if listed is None:
        return True, f"git cannot diff {tag} against HEAD, so every input counts as changed"
    matcher = load("check_reuse").matcher(check.inputs) if check.inputs else None
    changed = [p for p in listed.splitlines() if p and (matcher is None or matcher.match(p))]
    if not changed:
        return False, f"no path of its inputs changed since {tag}"
    return True, f"{len(changed)} path(s) of its inputs changed since {tag}, first {changed[0]}"


def _command(check: VerifyCheck) -> list[str]:
    argv = [WORKERS if part == "{workers}" else part for part in check.command]
    return ["uv", "run", *argv] if argv[0] == "pytest" else argv


def _timed(check: VerifyCheck, root: Path, run: Run) -> tuple[bool, str]:
    started = time.perf_counter()
    code = run(_command(check), root)
    seconds = time.perf_counter() - started
    return code == 0, f"{'pass' if code == 0 else 'FAIL'} in {seconds:.1f} s"


def run_checks(root: Path, run: Run = execute) -> Outcome:
    checks = load_verify_config(root).checks
    lines: list[str] = []
    passed = True
    for check in record_checks(checks):
        ok, said = _timed(check, root, run)
        passed = passed and ok
        lines.append(f"record check {check.name}: {said}")
    live = next((check for check in checks if check.name == LIVE_LEDGER), None)
    if live is None:
        lines.append(f"live-ledger tests: not declared, no {LIVE_LEDGER} check in the config")
        return Outcome(tuple(lines), passed)
    wanted, reason = live_ledger_reason(root, live)
    if not wanted:
        lines.append(f"live-ledger tests: skipped, {reason}")
        return Outcome(tuple(lines), passed)
    ok, said = _timed(live, root, run)
    lines.append(f"live-ledger tests: run, {reason}: {said}")
    return Outcome(tuple(lines), passed and ok)
