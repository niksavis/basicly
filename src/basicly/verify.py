from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

from . import tracker, tracker_paths, usage, verify_log, worktree
from .config import VERIFY_GATE_PROVIDER, VerifyCheck, VerifyConfig, load_verify_config
from .runner import sanitised_project_env
from .verify_artifact import write_run_artifact

if TYPE_CHECKING:
    from collections.abc import Sequence

DEFAULT_GATE = "verify"
GATE_PROVIDER = VERIFY_GATE_PROVIDER


def linked_worktree_guard(repo_root: Path) -> str | None:

    try:
        main = worktree.main_checkout(repo_root)
    except OSError, RuntimeError:
        return None
    root = Path(repo_root).resolve()
    if main == root:
        return None
    if tracker_paths.tracker_root(root).resolve() == main:
        return None
    redirect = (tracker_paths.LEDGER_DIR_NAME / tracker_paths.REDIRECT_NAME).as_posix()
    return (
        f"this checkout is a linked worktree of {main} without a {redirect} "
        "to it; a gate recorded here lives in the worktree's throwaway tracker "
        "copy and is discarded at landing. The loop records the verify gate from "
        "the base checkout when it lands the worktree — run without --issue "
        "here, or record the gate from the base checkout."
    )


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str
    returncode: int
    detail: str = ""
    output: str = ""
    command: tuple[str, ...] = ()
    duration_s: float = 0.0


@dataclass(frozen=True)
class VerifyReport:
    mode: str
    results: tuple[CheckResult, ...]

    @property
    def passed(self) -> bool:
        return not any(r.status == "fail" for r in self.results)

    @property
    def failures(self) -> tuple[str, ...]:
        return tuple(r.name for r in self.results if r.status == "fail")


_REMEDY_CHARS = 400


def check_remedy(output: str, check: str) -> str | None:

    label = f"{check}:"
    lines = [line.strip() for line in output.splitlines() if line.strip().startswith(label)]
    if not lines:
        return None
    joined = " · ".join(line.removeprefix(label).strip() for line in lines)
    return joined if len(joined) <= _REMEDY_CHARS else joined[:_REMEDY_CHARS] + "…"


RELEASE_NOTES_CHECK = "release-notes"
RELEASE_NOTES_LANDING_FLAG = "--landing"


def release_note_debt(repo_root: Path, tree: Path, mode: str, bead: str) -> str | None:

    declared = load_verify_config(repo_root).checks
    check = next((item for item in declared if item.name == RELEASE_NOTES_CHECK), None)
    if check is None:
        return None
    asked = replace(check, command=(*check.command, RELEASE_NOTES_LANDING_FLAG, bead))
    result = run_check(asked, tree, mode, capture=True)
    if result.status != "fail":
        return None
    said = (check_remedy(result.output, check.name) or result.detail or result.output).strip()
    return said or f"{check.name} failed and printed nothing"


def staged_files(repo_root: Path, suffix: str) -> list[str] | None:

    try:
        proc = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],  # noqa: S607 — PATH git
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    return [line for line in proc.stdout.splitlines() if line.endswith(suffix)]


def run_check(
    check: VerifyCheck, repo_root: Path, mode: str, *, capture: bool = False
) -> CheckResult:

    result = _run(check, list(check.command), repo_root, mode, capture=capture)
    if result.status == "pass":
        usage.record_verify_check(repo_root, check.name)
    return result


def run_fix(check: VerifyCheck, repo_root: Path, mode: str) -> CheckResult:

    if not check.fix_command:
        return CheckResult(check.name, "skip", 0)
    return _run(check, list(check.fix_command), repo_root, mode)


def _run(
    check: VerifyCheck,
    command: list[str],
    repo_root: Path,
    mode: str,
    *,
    capture: bool = False,
) -> CheckResult:

    name = check.name
    if mode == "staged" and check.staged_suffix:
        files = staged_files(repo_root, check.staged_suffix)
        if files is None:
            return CheckResult(
                name,
                "fail",
                1,
                "git diff --cached failed — cannot determine staged files, "
                "refusing to skip the check",
                command=tuple(command),
            )
        if not files:
            return CheckResult(name, "skip", 0)
        command += files
    started = time.perf_counter()
    try:
        proc = _spawn(command, repo_root, capture=capture)
    except FileNotFoundError:
        return CheckResult(
            name,
            "fail",
            127,
            f"command not found: {command[0]} — install it or edit "
            f"[[verify.checks]] in basicly.toml",
            command=tuple(command),
        )
    except OSError as exc:
        return CheckResult(
            name,
            "fail",
            126,
            f"cannot run {command[0]} ({exc.strerror or exc}) — check "
            f"[[verify.checks]] in basicly.toml",
            command=tuple(command),
        )
    output = f"{proc.stdout or ''}{proc.stderr or ''}"
    failed = proc.returncode != 0
    detail = (
        verify_log.pointer(verify_log.write(repo_root, name, output), repo_root)
        if failed and not capture
        else ""
    )
    return CheckResult(
        name,
        "fail" if failed else "pass",
        proc.returncode,
        detail=detail,
        output=output if capture else "",
        command=tuple(command),
        duration_s=round(time.perf_counter() - started, 3),
    )


def _spawn(
    command: Sequence[str], repo_root: Path, *, capture: bool
) -> subprocess.CompletedProcess[str]:

    env = sanitised_project_env(os.environ, repo_root)
    if capture:
        return subprocess.run(  # noqa: S603 — repo-declared argv, list form, no shell
            command, cwd=repo_root, env=env, check=False, capture_output=True, text=True
        )
    with subprocess.Popen(  # noqa: S603 — same argv, same trust boundary
        command,
        cwd=repo_root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    ) as proc:
        kept: list[str] = []
        for line in proc.stdout or ():
            sys.stdout.write(line)
            kept.append(line)
        sys.stdout.flush()
    return subprocess.CompletedProcess(list(command), proc.returncode, "".join(kept), "")


def run_verify(repo_root: Path, mode: str, config: VerifyConfig | None = None) -> VerifyReport:

    config = config or load_verify_config(repo_root)
    results = tuple(run_check(check, repo_root, mode) for check in config.for_mode(mode))
    report = VerifyReport(mode=mode, results=results)
    write_run_artifact(repo_root, report)
    return report


def rerun_failures(
    report: VerifyReport,
    repo_root: Path,
    mode: str,
    config: VerifyConfig | None = None,
    *,
    capture: bool = False,
) -> VerifyReport:

    failed = set(report.failures)
    if not failed:
        return report
    config = config or load_verify_config(repo_root)
    checks = [check for check in config.for_mode(mode) if check.name in failed]
    if not checks:
        return report
    return VerifyReport(
        mode=mode,
        results=tuple(run_check(c, repo_root, mode, capture=capture) for c in checks),
    )


DEPENDENCY_DEFECT_SIGNATURES: tuple[tuple[tuple[str, ...], str], ...] = (
    (
        ("LockUnavailableError", "another writer holds"),
        "the ledger serialises every append behind one lock and fails the write "
        "outright when it cannot take it before the timeout, so a gate contends with "
        "whatever else drives the tracker at that moment; no diff can make that "
        "contention its own fault (R8 in docs/architecture/architecture.md §32.9)",
    ),
)


def _defect_reason(output: str) -> str | None:
    for line in output.splitlines():
        for substrings, reason in DEPENDENCY_DEFECT_SIGNATURES:
            if all(s in line for s in substrings):
                return reason
    return None


def dependency_defect(report: VerifyReport) -> str | None:

    failures = [r for r in report.results if r.status == "fail"]
    if not failures:
        return None
    reasons: list[str] = []
    for result in failures:
        reason = _defect_reason(result.output) if result.output else None
        if reason is None:
            return None
        reasons.append(f"{result.name}: {reason}")
    return "; ".join(reasons)


def apply_fixes(repo_root: Path, mode: str, config: VerifyConfig | None = None) -> VerifyReport:

    config = config or load_verify_config(repo_root)
    results = tuple(run_fix(check, repo_root, mode) for check in config.for_mode(mode))
    return VerifyReport(mode=mode, results=results)


def report_gate(
    repo_root: Path,
    issue_id: str,
    report: VerifyReport,
    gate: str = DEFAULT_GATE,
    *,
    actor: str | None = None,
) -> tuple[bool, str]:

    status = "pass" if report.passed else "fail"
    detail = ", ".join(f"{r.name}={r.status}" for r in report.results) or "no checks"
    note = f"verify {report.mode}: {detail}"
    args = [
        "gate",
        "report",
        "--gate",
        gate,
        "--provider",
        GATE_PROVIDER,
        "--status",
        status,
        "--note",
        note,
    ]
    if actor:
        args += ["--actor", actor]
    args.append(issue_id)
    try:
        appended = tracker.write(repo_root, args)
    except RuntimeError as exc:
        return False, f"gate {gate} NOT recorded on {issue_id}: {exc}"
    if not appended:
        return True, f"gate {gate}={status} on {issue_id} was already held; kept the first"
    return True, f"recorded gate {gate}={status} on {issue_id}"
