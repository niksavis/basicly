"""Config-driven verify runner — the harness's deterministic gate.

Runs the checks declared in ``[verify]`` of ``basicly.toml`` for a given mode
(``fast`` / ``full`` / ``staged``), collecting a pass/fail/skip verdict per
check. When an issue id is supplied it records the aggregate verdict as a gate
via ``br gate report``. The block-vs-advise policy (which gates are required,
the rework rule) lives in the gate/checkpoint engine, not here — this runner
only produces and records the verdict.

Check subprocess output streams straight to the terminal (it is not captured),
so the consumer sees each tool's own output live.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import br, worktree
from .config import VerifyCheck, VerifyConfig, load_verify_config

DEFAULT_GATE = "verify"
GATE_PROVIDER = "basicly-verify"


def linked_worktree_guard(repo_root: Path) -> str | None:
    """Reason recording a gate from *repo_root* would lose it, or None when safe.

    A linked git worktree whose ``.beads`` redirects to the base checkout (the
    ``redirect`` file provisioning writes) shares the one real tracker, so
    recording from it is safe. Without the redirect, the worktree carries its
    own throwaway tracker copy and a gate recorded there never reaches the base
    checkout — it is discarded at landing.
    """
    try:
        main = worktree.main_checkout(repo_root)
    except OSError, RuntimeError:
        # worktree.run wraps any git failure in RuntimeError — outside a git
        # checkout there is no landing to lose the gate to.
        return None
    root = Path(repo_root).resolve()
    if main == root:
        return None
    redirect = root / ".beads" / "redirect"
    if redirect.is_file():
        try:
            target = Path(redirect.read_text(encoding="utf-8").strip()).resolve()
        except OSError:
            target = None
        if target == main / ".beads":
            return None  # shared tracker — the record lands in the base checkout
    return (
        f"this checkout is a linked worktree of {main} without a .beads/redirect "
        "to it; a gate recorded here lives in the worktree's throwaway tracker "
        "copy and is discarded at landing. The loop records the verify gate from "
        "the base checkout when it lands the worktree — run without --issue "
        "here, or record the gate from the base checkout."
    )


@dataclass(frozen=True)
class CheckResult:
    """The outcome of one verify check."""

    name: str
    status: str  # "pass" | "fail" | "skip"
    returncode: int
    # One-line human-readable context for a failure the tool itself could not
    # report (e.g. the command was not found on PATH).
    detail: str = ""


@dataclass(frozen=True)
class VerifyReport:
    """The aggregate outcome of a verify run."""

    mode: str
    results: tuple[CheckResult, ...]

    @property
    def passed(self) -> bool:
        """True when no check failed (skips and an empty run count as passing)."""
        return not any(r.status == "fail" for r in self.results)

    @property
    def failures(self) -> tuple[str, ...]:
        """Names of the checks that failed."""
        return tuple(r.name for r in self.results if r.status == "fail")


def staged_files(repo_root: Path, suffix: str) -> list[str] | None:
    """Staged (added/copied/modified) files ending in *suffix*; None if git failed.

    None and [] are deliberately distinct: an empty list means "nothing staged"
    (the check may skip), None means the git call itself failed — a lost gate
    must never pass unnoticed, so callers must fail the check.
    """
    try:
        proc = subprocess.run(  # nosec B603 B607
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
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


def run_check(check: VerifyCheck, repo_root: Path, mode: str) -> CheckResult:
    """Run a single check, filtering to staged files in ``staged`` mode."""
    command = list(check.command)
    if mode == "staged" and check.staged_suffix:
        files = staged_files(repo_root, check.staged_suffix)
        if files is None:
            return CheckResult(
                check.name,
                "fail",
                1,
                "git diff --cached failed — cannot determine staged files, "
                "refusing to skip the check",
            )
        if not files:
            return CheckResult(check.name, "skip", 0)
        command += files
    try:
        proc = subprocess.run(command, cwd=repo_root, check=False)  # nosec B603
    except FileNotFoundError:
        return CheckResult(
            check.name,
            "fail",
            127,
            f"command not found: {command[0]} — install it or edit "
            f"[[verify.checks]] in basicly.toml",
        )
    except OSError as exc:
        # e.g. PermissionError: a PATH candidate exists but is not executable
        # (common on WSL with Windows mounts on PATH). Same contract: a failed
        # check with a one-line reason, never a traceback.
        return CheckResult(
            check.name,
            "fail",
            126,
            f"cannot run {command[0]} ({exc.strerror or exc}) — check "
            f"[[verify.checks]] in basicly.toml",
        )
    return CheckResult(check.name, "pass" if proc.returncode == 0 else "fail", proc.returncode)


def run_verify(repo_root: Path, mode: str, config: VerifyConfig | None = None) -> VerifyReport:
    """Run every check configured for *mode* and collect the results."""
    config = config or load_verify_config(repo_root)
    results = tuple(run_check(check, repo_root, mode) for check in config.for_mode(mode))
    return VerifyReport(mode=mode, results=results)


def report_gate(
    repo_root: Path, issue_id: str, report: VerifyReport, gate: str = DEFAULT_GATE
) -> tuple[bool, str]:
    """Record the verdict on *issue_id* via ``br gate report``.

    Returns ``(ok, message)``; degrades gracefully (returns ``False`` with
    guidance) when ``br`` is not on PATH or the command fails, rather than
    raising, so a missing tracker never masks the verify result itself.
    """
    status = "pass" if report.passed else "fail"
    detail = ", ".join(f"{r.name}={r.status}" for r in report.results) or "no checks"
    note = f"verify {report.mode}: {detail}"
    proc = br.try_run_br(
        repo_root,
        [
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
            issue_id,
        ],
    )
    if proc is None:
        return False, "br not on PATH; gate not recorded"
    if proc.returncode != 0:
        return False, f"br gate report failed: {(proc.stderr or proc.stdout).strip()}"
    return True, f"recorded gate {gate}={status} on {issue_id}"
