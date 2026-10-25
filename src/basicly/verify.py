from __future__ import annotations

import subprocess
import sys
import time
import uuid
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from . import (
    affected,
    pyright_inputs,
    pytest_inputs,
    tracker,
    tracker_paths,
    usage,
    verify_cache,
    verify_log,
    worktree,
)
from .config import VERIFY_GATE_PROVIDER, VerifyCheck, VerifyConfig, load_verify_config
from .hook_runtime import load as _hook_runtime
from .runner import sanitised_project_env
from .verify_artifact import (
    CheckResult,
    VerifyReport,
    blocked_result,
    cached_result,
    receipt_result,
    write_run_artifact,
)
from .verify_receipt import CheckReceipt, supported_command

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
    try:
        shared = tracker_paths.tracker_root(root).resolve()
    except tracker_paths.RedirectError as exc:
        return str(exc)
    if shared == main:
        return None
    redirect = (tracker_paths.LEDGER_DIR_NAME / tracker_paths.REDIRECT_NAME).as_posix()
    return (
        f"this checkout is a linked worktree of {main} without a {redirect} "
        "to it; a gate recorded here lives in the worktree's throwaway tracker "
        "copy and is discarded at landing. The loop records the verify gate from "
        "the base checkout when it lands the worktree — run without --issue "
        "here, or record the gate from the base checkout."
    )


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
            ["git", "diff", "--cached", "--name-only", "-z", "--diff-filter=ACM"],  # noqa: S607 — PATH git
            cwd=repo_root,
            env=affected.environment(repo_root),
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    return [path for path in proc.stdout.split("\0") if path.endswith(suffix)]


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
        command += [f"./{path}" if path.startswith("-") else path for path in files]
    started = time.perf_counter()
    try:
        if check.reuse == "pyright" and supported_command(tuple(command), mode):
            proc = _spawn(command, repo_root, capture=capture, controlled="pyright")
        elif check.reuse == "pytest" and pytest_inputs.supported_command(tuple(command), mode):
            proc = _spawn(command, repo_root, capture=capture, controlled="pytest")
        else:
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
        output=output,
        command=tuple(command),
        duration_s=round(time.perf_counter() - started, 3),
    )


def _spawn(
    command: Sequence[str], repo_root: Path, *, capture: bool, controlled: str = ""
) -> subprocess.CompletedProcess[str]:

    command = _hook_runtime("command_paths").resolve_command(command, repo_root)
    env = sanitised_project_env(affected.environment(repo_root), repo_root)
    if controlled == "pyright":
        env = pyright_inputs.execution_environment(repo_root, env)
    elif controlled == "pytest":
        env = pytest_inputs.execution_environment(env)
    if capture:
        return subprocess.run(  # noqa: S603 — repo-declared argv, list form, no shell
            command,
            cwd=repo_root,
            env=env,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="backslashreplace",
        )
    with subprocess.Popen(  # noqa: S603 — same argv, same trust boundary
        command,
        cwd=repo_root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="backslashreplace",
        bufsize=1,
    ) as proc:
        kept: list[str] = []
        for line in proc.stdout or ():
            sys.stdout.write(line)
            kept.append(line)
        sys.stdout.flush()
    return subprocess.CompletedProcess(list(command), proc.returncode, "".join(kept), "")


def _inputs(repo_root: Path, recipe: str = "pyright") -> tuple[str, str]:
    try:
        provider = pytest_inputs if recipe == "pytest" else pyright_inputs
        return provider.fingerprint(repo_root), ""
    except pyright_inputs.UnsupportedInputsError as exc:
        return "", str(exc)


def _proof_inputs(root: Path, recipe: str, proofs: dict[str, tuple[str, str]]) -> tuple[str, str]:
    if recipe not in proofs:
        proofs[recipe] = _inputs(root, recipe)
    return proofs[recipe]


def _refresh_unstable_results(
    repo_root: Path,
    mode: str,
    config: VerifyConfig,
    results: list[CheckResult],
    reason: tuple[str, str],
) -> None:
    blocked_by = next((r.name for r in results if config.fail_fast and r.status == "fail"), "")
    recipe, explanation = reason
    for index, (check, result) in enumerate(zip(config.for_mode(mode), results, strict=True)):
        if not result.reused_from or check.reuse != recipe:
            continue
        if blocked_by:
            results[index] = blocked_result(check.name, check.command, blocked_by)
            continue
        results[index] = replace(
            run_check(check, repo_root, mode),
            reuse_reason=explanation or "inputs changed during verification",
        )
        if config.fail_fast and results[index].status == "fail":
            blocked_by = check.name


def _finish_input_proof(
    repo_root: Path,
    mode: str,
    config: VerifyConfig,
    results: list[CheckResult],
    proof: tuple[str, str],
) -> bool:
    recipe, before = proof
    if not before or (
        not all(r.status == "pass" for r in results) and not any(r.reused_from for r in results)
    ):
        return False
    after, reason = _inputs(repo_root, recipe)
    if before == after:
        return True
    _refresh_unstable_results(repo_root, mode, config, results, (recipe, reason))
    return False


def run_verify(
    repo_root: Path,
    mode: str,
    config: VerifyConfig | None = None,
    *,
    capture_receipts: bool = False,
    reuse: VerifyReport | None = None,
) -> VerifyReport:

    started = time.perf_counter()
    config = config or load_verify_config(repo_root)
    checks = config.for_mode(mode)
    selection = (
        affected.select(repo_root, config.affected, [check.name for check in checks])
        if affected.active(repo_root)
        else None
    )
    if selection and selection["invalid_snapshot"]:
        report = VerifyReport(
            mode,
            (CheckResult("affected-provider", "fail", 1, selection["fallback"]),),
            selection=selection,
        )
        write_run_artifact(repo_root, report)
        return report
    eligible = {
        check.name
        for check in checks
        if (check.reuse == "pyright" and supported_command(check.command, mode))
        or (check.reuse == "pytest" and pytest_inputs.supported_command(check.command, mode))
    }
    caching = config.cache and selection is None
    proofs: dict[str, tuple[str, str]] = {}
    before, reason, cache_reason = "", "", ""
    cached: tuple[verify_cache.CachedCheck, ...] = ()
    results: list[CheckResult] = []
    blocked_by = ""
    for check in checks:
        if selection is not None and check.name not in selection["selected"]:
            results.append(CheckResult(check.name, "skip", 0, selection["reasons"][check.name]))
            continue
        if blocked_by:
            result = blocked_result(check.name, check.command, blocked_by)
            print(f"==> {check.name} NOT RUN: {result.detail}", flush=True)
            results.append(result)
            continue
        if check.name in eligible and (capture_receipts or reuse is not None or caching):
            before, reason = _proof_inputs(repo_root, check.reuse or "", proofs)
            cached, cache_reason = verify_cache.read(repo_root) if caching and before else ((), "")
        borrowed = (
            receipt_result(check.name, check.command, mode, before, reuse)
            if check.name in eligible
            else None
        )
        if borrowed is None and check.name in eligible and before:
            borrowed = cached_result(check.name, check.command, (mode, before), cached)
        if borrowed is not None:
            print(f"{check.name}: reused verified result from {borrowed.reused_from}", flush=True)
            results.append(borrowed)
            continue
        print(f"==> {check.name}", flush=True)
        result = run_check(check, repo_root, mode)
        if check.reuse and (capture_receipts or reuse is not None or caching):
            explanation = "unsupported command or mode"
            if check.name in eligible:
                explanation = (
                    reason
                    or cache_reason
                    or (
                        "missing or changed receipt inputs"
                        if reuse
                        else "executed to establish receipt"
                    )
                )
            result = replace(result, reuse_reason=explanation, input_fingerprint=before)
        results.append(result)
        if config.fail_fast and result.status == "fail":
            blocked_by = check.name
    stable = {
        recipe: _finish_input_proof(repo_root, mode, config, results, (recipe, proof[0]))
        for recipe, proof in proofs.items()
    }
    run_id = uuid.uuid4().hex
    passed = all(r.status == "pass" for r in results)
    receipts = (
        tuple(
            CheckReceipt(check.name, check.command, mode, proofs[check.reuse or ""][0], run_id)
            for check, result in zip(checks, results, strict=True)
            if check.name in eligible and stable.get(check.reuse or "", False)
        )
        if capture_receipts and passed
        else ()
    )
    report = VerifyReport(
        mode, tuple(results), run_id, receipts, round(time.perf_counter() - started, 3), selection
    )
    write_run_artifact(repo_root, report)
    if caching and stable and passed:
        verify_cache.write(
            repo_root,
            tuple(
                verify_cache.CachedCheck(
                    check.name,
                    check.command,
                    mode,
                    proofs[check.reuse or ""][0],
                    result.reused_from or run_id,
                    result.source_duration_s if result.reused_from else result.duration_s,
                )
                for check, result in zip(checks, results, strict=True)
                if check.name in eligible and stable.get(check.reuse or "", False)
            ),
            overhead_s=max(0, report.duration_s - sum(result.duration_s for result in results)),
        )
    return report


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
