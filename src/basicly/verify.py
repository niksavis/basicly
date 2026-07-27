"""Config-driven verify runner — the harness's deterministic gate.

Runs the checks declared in ``[verify]`` of ``basicly.toml`` for a given mode
(``fast`` / ``full`` / ``staged``), collecting a pass/fail/skip verdict per
check. When an issue id is supplied it records the aggregate verdict as a gate
via ``br gate report``. The block-vs-advise policy (which gates are required,
the rework rule) lives in the gate/checkpoint engine, not here — this runner
only produces and records the verdict.

A check may declare a ``fix_command`` — a deterministic, lossless repair such as
a formatter's write mode. ``apply_fixes`` runs those and nothing else; it is
never part of a plain verify run, so the verdict a consumer (or CI) gets from
``run_verify`` is unchanged.

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
    # Combined stdout+stderr, populated only when the caller asked to capture
    # (basicly-kjc5.56). Empty for a streamed run, which is every normal one —
    # a gate's output belongs on the operator's terminal, not in memory.
    output: str = ""


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


def run_check(
    check: VerifyCheck, repo_root: Path, mode: str, *, capture: bool = False
) -> CheckResult:
    """Run a single check, filtering to staged files in ``staged`` mode."""
    return _run(check, list(check.command), repo_root, mode, capture=capture)


def run_fix(check: VerifyCheck, repo_root: Path, mode: str) -> CheckResult:
    """Apply *check*'s mechanical repair; skip when it declares no ``fix_command``.

    A fix is never a verdict: the check that follows is what decides pass/fail,
    so a failing fixer is reported (status ``fail``) but does not stand in for
    the gate.
    """
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
    """Run one check-or-fix command, filtering to staged files in ``staged`` mode.

    *command* is passed separately because ``run_fix`` runs the check's
    ``fix_command`` rather than its ``command``; everything else comes off *check*.

    ``capture`` diverts the command's output into ``CheckResult.output`` instead of
    the terminal. Off by default and only ever set for the diagnostic re-run, so
    an operator watching a long gate still sees it stream.
    """
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
            )
        if not files:
            return CheckResult(name, "skip", 0)
        command += files
    try:
        proc = subprocess.run(  # nosec B603
            command,
            cwd=repo_root,
            check=False,
            capture_output=capture,
            text=capture or None,
        )
    except FileNotFoundError:
        return CheckResult(
            name,
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
            name,
            "fail",
            126,
            f"cannot run {command[0]} ({exc.strerror or exc}) — check "
            f"[[verify.checks]] in basicly.toml",
        )
    output = f"{proc.stdout or ''}{proc.stderr or ''}" if capture else ""
    return CheckResult(
        name, "pass" if proc.returncode == 0 else "fail", proc.returncode, output=output
    )


def run_verify(repo_root: Path, mode: str, config: VerifyConfig | None = None) -> VerifyReport:
    """Run every check configured for *mode* and collect the results."""
    config = config or load_verify_config(repo_root)
    results = tuple(run_check(check, repo_root, mode) for check in config.for_mode(mode))
    return VerifyReport(mode=mode, results=results)


def rerun_failures(
    report: VerifyReport,
    repo_root: Path,
    mode: str,
    config: VerifyConfig | None = None,
    *,
    capture: bool = False,
) -> VerifyReport:
    """Re-run just the checks that failed in *report*, unchanged.

    Evidence, not a retry. Nothing in the tree, the config, or the command line
    differs between the two runs, so a check that passes now did not fail on the
    work under test — which is what lets a caller tell an unreliable gate from a
    merit failure instead of scoring both the same (basicly-55yh).

    Only the failed checks re-run, and the whole check re-runs rather than the
    one case inside it that failed: narrowing to a single test would change the
    run, and an order-dependent test that passes alone would then be excused as a
    flake when it is a real defect.

    Fail-safe by construction. A green *report* is returned unchanged, and so is
    one whose failures no longer match a configured check — no new evidence means
    the original verdict stands. A caller may only forgive on a positive pass
    here, never on the absence of a result.

    ``capture`` collects the re-run's output so a caller can also ask
    :func:`dependency_defect` about a failure that *did* reproduce
    (basicly-kjc5.56).
    """
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


# Failure signatures a dependency emits and the work under test cannot cause
# (basicly-kjc5.56). Each entry is (substring, why it is safe to forgive) and
# earns its place only on proof that no change to this repo can produce it —
# otherwise this becomes a way to launder a real failure, which is worse than the
# flake it excuses. Keep it short and keep the reason with it.
DEPENDENCY_DEFECT_SIGNATURES: tuple[tuple[str, str], ...] = (
    (
        "Validation failed: updated_at: cannot be before created_at",
        "br rejects its own write when the host clock steps backwards between two "
        "writes; nothing in this repo sets either timestamp (basicly-vkh0.6 carries "
        "it as a requirement on the replacement)",
    ),
)


def dependency_defect(report: VerifyReport) -> str | None:
    """The reason a failure in *report* is a known dependency defect, else None.

    Matched on captured output, so it answers only for a report produced with
    ``capture=True``; a streamed report has no text and yields None, which keeps
    the original verdict standing rather than forgiving on absent evidence.

    This exists because the re-run test alone cannot see this class of defect. A
    backwards clock step persists for a window, so the failure reproduces and
    scores as a merit failure — measured on basicly-m4zv.9, where a landing spent
    a rework attempt on it (basicly-55yh shipped the re-run; this closes the gap).
    """
    for result in report.results:
        if result.status != "fail" or not result.output:
            continue
        for signature, reason in DEPENDENCY_DEFECT_SIGNATURES:
            if signature in result.output:
                return f"{result.name}: {reason}"
    return None


def apply_fixes(repo_root: Path, mode: str, config: VerifyConfig | None = None) -> VerifyReport:
    """Apply the ``fix_command`` of every *mode* check that declares one.

    Deliberately separate from ``run_verify``: a plain verify run stays a pure
    verdict, so CI still fails on unformatted input from outside the harness. The
    fix step is opt-in (``basicly verify --fix``, the pre-commit hook) and its
    results are advisory — the checks that follow produce the gate.
    """
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
    """Record the verdict on *issue_id* via ``br gate report``.

    When the dispatched runner is known (basicly-140a), *actor* is recorded as the
    gate's audit-trail actor, so a gate result ties to the agent that produced it.
    It is optional — a gate reported outside a dispatch records no actor. (Model
    provenance rides the landing commit trailer, not the gate's free-text note.)

    Returns ``(ok, message)``; degrades gracefully (returns ``False`` with
    guidance) when ``br`` is not on PATH or the command fails, rather than
    raising, so a missing tracker never masks the verify result itself.
    """
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
    proc = br.try_run_br(repo_root, args)
    if proc is None:
        return False, "br not on PATH; gate not recorded"
    if proc.returncode != 0:
        return False, f"br gate report failed: {(proc.stderr or proc.stdout).strip()}"
    return True, f"recorded gate {gate}={status} on {issue_id}"
